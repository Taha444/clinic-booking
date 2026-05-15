import os
import threading
import html
from flask import Flask, render_template, request, redirect, session, jsonify, flash
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import FlaskForm, CSRFProtect
from wtforms import StringField, IntegerField, TelField, DateField, SelectField, SubmitField, PasswordField, TextAreaField
from wtforms.validators import DataRequired, NumberRange, Length, Regexp, Optional
from datetime import datetime
import pytz
from dotenv import load_dotenv
import smtplib
from email.mime.text import MIMEText
from werkzeug.security import check_password_hash
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from sqlalchemy import UniqueConstraint
from sqlalchemy.exc import IntegrityError

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY")

app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Strict'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///clinic.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

csrf = CSRFProtect(app)
limiter = Limiter(get_remote_address, app=app)


@app.after_request
def add_security_headers(response):
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains; preload'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


# ══════════════════════════════════════════
#  MODELS
# ══════════════════════════════════════════

class Booking(db.Model):
    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(100), nullable=False)
    age         = db.Column(db.Integer, nullable=False)
    phone       = db.Column(db.String(20), nullable=False)
    pain        = db.Column(db.String(200), nullable=False)
    conditions  = db.Column(db.String(200), nullable=True)
    date        = db.Column(db.String(20), nullable=False)
    appointment = db.Column(db.String(20), nullable=False)

    __table_args__ = (
        UniqueConstraint('date', 'appointment', name='uq_date_appointment'),
    )


class PatientProfile(db.Model):
    """ملف المريض — يُنشأ تلقائياً أول حجز، ويُحدَّث مع كل حجز جديد"""
    id              = db.Column(db.Integer, primary_key=True)
    name            = db.Column(db.String(100), nullable=False)
    phone           = db.Column(db.String(20), nullable=False, unique=True, index=True)
    age             = db.Column(db.Integer)
    conditions      = db.Column(db.String(300))          # الحالات المزمنة
    first_visit     = db.Column(db.String(20))           # تاريخ أول زيارة
    last_visit      = db.Column(db.String(20))           # تاريخ آخر زيارة
    total_visits    = db.Column(db.Integer, default=0)
    # ملاحظات الدكتور العامة على المريض
    doctor_notes    = db.Column(db.Text, nullable=True)
    created_at      = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at      = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # علاقة: ملاحظات الجلسات
    session_notes   = db.relationship('SessionNote', backref='patient',
                                      lazy=True, order_by='SessionNote.date.desc()')


class SessionNote(db.Model):
    """ملاحظة الدكتور على كل جلسة"""
    id           = db.Column(db.Integer, primary_key=True)
    patient_id   = db.Column(db.Integer, db.ForeignKey('patient_profile.id'), nullable=False)
    booking_id   = db.Column(db.Integer, db.ForeignKey('booking.id'), nullable=True)
    date         = db.Column(db.String(20), nullable=False)
    appointment  = db.Column(db.String(20))
    complaint    = db.Column(db.String(300))             # الشكوى في هذه الجلسة
    diagnosis    = db.Column(db.Text)                   # التشخيص
    treatment    = db.Column(db.Text)                   # العلاج المُعطى
    progress     = db.Column(db.String(50))             # التحسن: ممتاز / جيد / لا تحسن / تراجع
    next_session = db.Column(db.String(200))            # توصيات الجلسة القادمة
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)


with app.app_context():
    db.create_all()


# ══════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════

all_slots = []
for hour in range(3, 11):
    all_slots.append(f"{hour}:00 PM")
    all_slots.append(f"{hour}:30 PM")


def get_egypt_today():
    return datetime.now(pytz.timezone('Africa/Cairo')).date()


def is_valid_booking_date(date_str):
    try:
        selected_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        today = get_egypt_today()
        if selected_date.weekday() == 4:
            return False, "العيادة مغلقة يوم الجمعة"
        if selected_date < today:
            return False, "لا يمكن الحجز في تاريخ سابق"
        return True, selected_date
    except ValueError:
        return False, "تاريخ غير صالح"


def get_booked_slots(date):
    return [b.appointment for b in Booking.query.filter_by(date=date).all()]


def upsert_patient_profile(booking):
    """إنشاء أو تحديث ملف المريض بعد كل حجز"""
    profile = PatientProfile.query.filter_by(phone=booking.phone).first()
    if profile:
        # تحديث بيانات موجودة
        profile.last_visit   = booking.date
        profile.total_visits = (profile.total_visits or 0) + 1
        profile.age          = booking.age
        # دمج الحالات (إضافة جديدة بدون تكرار)
        if booking.conditions:
            old = set(c.strip() for c in (profile.conditions or '').split(',') if c.strip())
            new = set(c.strip() for c in booking.conditions.split(',') if c.strip())
            profile.conditions = ', '.join(old | new)
        profile.updated_at = datetime.utcnow()
    else:
        profile = PatientProfile(
            name         = booking.name,
            phone        = booking.phone,
            age          = booking.age,
            conditions   = booking.conditions,
            first_visit  = booking.date,
            last_visit   = booking.date,
            total_visits = 1,
        )
        db.session.add(profile)
    db.session.flush()   # عشان نجيب الـ id لو جديد
    return profile


# ══════════════════════════════════════════
#  FORMS
# ══════════════════════════════════════════

class BookingForm(FlaskForm):
    name = StringField('الاسم', validators=[
        DataRequired(), Length(min=3),
        Regexp(r'^[أ-يa-zA-Z\s]+$', message="حروف فقط")
    ])
    age  = IntegerField('العمر', validators=[DataRequired(), NumberRange(min=1, max=120)])
    phone = TelField('رقم الهاتف', validators=[
        DataRequired(), Regexp(r'^\d{10,}$', message="10 أرقام على الأقل")
    ])
    pain = StringField('بماذا تشعر؟', validators=[
        DataRequired(), Length(max=200),
        Regexp(r'^[^<>"\']+$', message="رموز غير مسموح بها")
    ])
    date        = DateField('تاريخ الحجز', validators=[DataRequired()])
    appointment = SelectField('ميعاد الحجز', validators=[DataRequired()], choices=[])
    submit      = SubmitField('احجز الآن')


class LoginForm(FlaskForm):
    username = StringField('اسم المستخدم', validators=[DataRequired()])
    password = PasswordField('كلمة السر',  validators=[DataRequired()])
    submit   = SubmitField('تسجيل الدخول')


class SessionNoteForm(FlaskForm):
    complaint    = StringField('الشكوى', validators=[Optional(), Length(max=300)])
    diagnosis    = TextAreaField('التشخيص', validators=[Optional(), Length(max=1000)])
    treatment    = TextAreaField('العلاج المُعطى', validators=[Optional(), Length(max=1000)])
    progress     = SelectField('مستوى التحسن', choices=[
        ('', '— اختر —'),
        ('ممتاز', '✅ ممتاز'),
        ('جيد', '👍 جيد'),
        ('لا تحسن', '➖ لا تحسن'),
        ('تراجع', '⚠️ تراجع'),
    ], validators=[Optional()])
    next_session = StringField('توصيات الجلسة القادمة', validators=[Optional(), Length(max=300)])
    submit       = SubmitField('حفظ الملاحظة')


class DoctorNotesForm(FlaskForm):
    doctor_notes = TextAreaField('ملاحظات الدكتور العامة', validators=[Optional(), Length(max=2000)])
    submit       = SubmitField('حفظ')


# ══════════════════════════════════════════
#  PUBLIC ROUTES
# ══════════════════════════════════════════

@app.route('/')
def index():
    today_str = get_egypt_today().strftime('%Y-%m-%d')
    date      = request.args.get('date', today_str)
    valid, _  = is_valid_booking_date(date)
    if not valid:
        date = today_str

    booked          = get_booked_slots(date)
    available_times = [s for s in all_slots if s not in booked]

    form = BookingForm()
    form.appointment.choices = [(t, t) for t in available_times] if available_times else [('', 'لا توجد مواعيد')]
    form.date.data = datetime.strptime(date, '%Y-%m-%d')

    return render_template('index.html', form=form,
                           available_times=available_times, selected_date=date)


@app.route('/available_slots')
def available_slots():
    date = request.args.get('date')
    if not date:
        return jsonify({'available_times': [], 'error': 'التاريخ مطلوب'})
    valid, result = is_valid_booking_date(date)
    if not valid:
        return jsonify({'available_times': [], 'error': result})
    booked = get_booked_slots(date)
    return jsonify({'available_times': [s for s in all_slots if s not in booked]})


@app.route('/submit', methods=['POST'])
@limiter.limit("5 per minute")
def submit():
    name        = request.form.get('name', '').strip()
    age_str     = request.form.get('age', '').strip()
    phone       = request.form.get('phone', '').strip()
    pain        = request.form.get('pain', '').strip()
    date_str    = request.form.get('date', '').strip()
    appointment = request.form.get('appointment', '').strip()
    conditions  = request.form.getlist('conditions')

    errors = []
    if not name or len(name) < 3:
        errors.append("الاسم يجب أن يكون 3 أحرف على الأقل")
    try:
        age = int(age_str)
        if not (1 <= age <= 120):
            errors.append("العمر غير منطقي")
    except (ValueError, TypeError):
        errors.append("العمر غير صالح")
        age = 0
    if not phone.isdigit() or len(phone) < 10:
        errors.append("رقم الهاتف غير صالح")
    if not pain:
        errors.append("يرجى وصف الألم")
    if not date_str:
        errors.append("التاريخ مطلوب")
    else:
        valid, result = is_valid_booking_date(date_str)
        if not valid:
            errors.append(result)
    if not appointment:
        errors.append("يرجى اختيار ميعاد")
    elif date_str and appointment in get_booked_slots(date_str):
        errors.append("هذا الموعد محجوز بالفعل")

    if errors:
        for e in errors:
            flash(e, 'error')
        return redirect('/')

    try:
        booking = Booking(
            name        = html.escape(name),
            age         = age,
            phone       = phone,
            pain        = html.escape(pain),
            conditions  = ', '.join(conditions),
            date        = date_str,
            appointment = appointment,
        )
        db.session.add(booking)
        db.session.flush()           # نجيب الـ booking.id

        # ✅ إنشاء / تحديث ملف المريض تلقائياً
        upsert_patient_profile(booking)
        db.session.commit()

    except IntegrityError:
        db.session.rollback()
        flash("هذا الموعد محجوز بالفعل، يرجى اختيار وقت آخر.", 'error')
        return redirect('/')
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"DB error: {e}")
        flash("حدث خطأ، يرجى المحاولة مرة أخرى.", 'error')
        return redirect('/')

    message = f"""حجز جديد - مركز الهادي

الاسم: {name}
العمر: {age}
الهاتف: {phone}
التاريخ: {date_str}
الميعاد: {appointment}
الوصف: {pain}
الحالات: {', '.join(conditions) or 'لا يوجد'}"""
    threading.Thread(target=send_email_safe,
                     args=("tetoelsalahy@gmail.com", "حجز جديد - عيادة الهادي", message),
                     daemon=True).start()

    return redirect('/confirmation')


@app.route('/confirmation')
def confirmation():
    return render_template('confirmation.html')


# ══════════════════════════════════════════
#  ADMIN AUTH
# ══════════════════════════════════════════

@app.route('/login', methods=['GET', 'POST'])
@limiter.limit("3 per minute")
def login():
    form  = LoginForm()
    error = None
    if form.validate_on_submit():
        if (form.username.data == os.getenv("ADMIN_USERNAME") and
                check_password_hash(os.getenv("ADMIN_PASSWORD"), form.password.data)):
            session['admin_logged_in'] = True
            return redirect('/bookings')
        error = "بيانات الدخول غير صحيحة"
    return render_template('login.html', form=form, error=error)


@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')


def admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('admin_logged_in'):
            return redirect('/login')
        return f(*args, **kwargs)
    return decorated


# ══════════════════════════════════════════
#  ADMIN — BOOKINGS
# ══════════════════════════════════════════

@app.route('/bookings')
@admin_required
def bookings():
    search      = request.args.get('search', '').strip()
    date_filter = request.args.get('date_filter', '').strip()
    query = Booking.query
    if search:
        query = query.filter(db.or_(
            Booking.name.ilike(f'%{search}%'),
            Booking.phone.ilike(f'%{search}%')
        ))
    if date_filter:
        query = query.filter_by(date=date_filter)
    all_bookings = query.order_by(Booking.date, Booking.appointment).all()
    return render_template('bookings.html', bookings=all_bookings,
                           search=search, date_filter=date_filter)


@app.route('/delete_booking/<int:booking_id>', methods=['POST'])
@admin_required
def delete_booking(booking_id):
    booking = Booking.query.get_or_404(booking_id)
    db.session.delete(booking)
    db.session.commit()
    flash("تم حذف الحجز بنجاح", 'success')
    return redirect('/bookings')


# ══════════════════════════════════════════
#  ADMIN — PATIENTS
# ══════════════════════════════════════════

@app.route('/patients')
@admin_required
def patients():
    """قائمة كل المرضى"""
    search = request.args.get('search', '').strip()
    query  = PatientProfile.query
    if search:
        query = query.filter(db.or_(
            PatientProfile.name.ilike(f'%{search}%'),
            PatientProfile.phone.ilike(f'%{search}%'),
        ))
    all_patients = query.order_by(PatientProfile.last_visit.desc()).all()
    return render_template('patients.html', patients=all_patients, search=search)


@app.route('/patient/<int:patient_id>')
@admin_required
def patient_profile(patient_id):
    """ملف المريض الكامل"""
    patient = PatientProfile.query.get_or_404(patient_id)
    # كل الحجوزات المرتبطة برقم تليفونه
    bookings_history = (Booking.query
                        .filter_by(phone=patient.phone)
                        .order_by(Booking.date.desc())
                        .all())
    note_form   = SessionNoteForm()
    doctor_form = DoctorNotesForm(doctor_notes=patient.doctor_notes)
    return render_template('patient_profile.html',
                           patient=patient,
                           bookings_history=bookings_history,
                           note_form=note_form,
                           doctor_form=doctor_form)


@app.route('/patient/<int:patient_id>/add_note', methods=['POST'])
@admin_required
def add_session_note(patient_id):
    """إضافة ملاحظة جلسة جديدة"""
    patient = PatientProfile.query.get_or_404(patient_id)
    form    = SessionNoteForm()
    if form.validate_on_submit():
        note = SessionNote(
            patient_id   = patient.id,
            booking_id   = request.form.get('booking_id') or None,
            date         = request.form.get('note_date') or get_egypt_today().strftime('%Y-%m-%d'),
            appointment  = request.form.get('note_appointment', ''),
            complaint    = html.escape(form.complaint.data or ''),
            diagnosis    = html.escape(form.diagnosis.data or ''),
            treatment    = html.escape(form.treatment.data or ''),
            progress     = form.progress.data or '',
            next_session = html.escape(form.next_session.data or ''),
        )
        db.session.add(note)
        db.session.commit()
        flash("✅ تم حفظ ملاحظة الجلسة", 'success')
    else:
        flash("خطأ في البيانات، يرجى المراجعة", 'error')
    return redirect(f'/patient/{patient_id}')


@app.route('/patient/<int:patient_id>/update_notes', methods=['POST'])
@admin_required
def update_doctor_notes(patient_id):
    """تحديث الملاحظات العامة للدكتور"""
    patient = PatientProfile.query.get_or_404(patient_id)
    form    = DoctorNotesForm()
    if form.validate_on_submit():
        patient.doctor_notes = html.escape(form.doctor_notes.data or '')
        patient.updated_at   = datetime.utcnow()
        db.session.commit()
        flash("✅ تم حفظ الملاحظات", 'success')
    return redirect(f'/patient/{patient_id}')


@app.route('/patient/<int:patient_id>/delete_note/<int:note_id>', methods=['POST'])
@admin_required
def delete_session_note(patient_id, note_id):
    note = SessionNote.query.get_or_404(note_id)
    db.session.delete(note)
    db.session.commit()
    flash("تم حذف الملاحظة", 'success')
    return redirect(f'/patient/{patient_id}')


@app.route('/patient/<int:patient_id>/delete', methods=['POST'])
@admin_required
def delete_patient(patient_id):
    patient = PatientProfile.query.get_or_404(patient_id)
    # حذف كل ملاحظاته أولاً
    SessionNote.query.filter_by(patient_id=patient_id).delete()
    db.session.delete(patient)
    db.session.commit()
    flash("تم حذف ملف المريض", 'success')
    return redirect('/patients')


# ══════════════════════════════════════════
#  EMAIL
# ══════════════════════════════════════════

def send_email_safe(to, subject, body):
    try:
        sender   = os.getenv("EMAIL_SENDER", "elhadyclinic1@gmail.com")
        password = os.getenv("EMAIL_PASSWORD")
        if not password:
            return
        msg = MIMEText(body, 'plain', 'utf-8')
        msg['Subject'] = subject
        msg['From']    = sender
        msg['To']      = to
        with smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=10) as server:
            server.login(sender, password)
            server.sendmail(sender, to, msg.as_string())
    except Exception as e:
        app.logger.error(f"Email error (background): {e}")

if __name__ == '__main__':
    app.run(debug=False)

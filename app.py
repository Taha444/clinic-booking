import os, threading, html, csv, io, secrets
from functools import wraps
from datetime import datetime, timedelta
from flask import (Flask, render_template, request, redirect,
                   session, jsonify, flash, send_file, url_for)
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import FlaskForm, CSRFProtect
from wtforms import (StringField, IntegerField, TelField, DateField,
                     SelectField, SubmitField, PasswordField, TextAreaField)
from wtforms.validators import DataRequired, NumberRange, Length, Regexp, Optional
import pytz
from dotenv import load_dotenv
from werkzeug.security import check_password_hash
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from sqlalchemy import UniqueConstraint
from sqlalchemy.exc import IntegrityError

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", secrets.token_hex(32))

app.config['SESSION_COOKIE_SECURE']   = True
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Strict'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///clinic.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db     = SQLAlchemy(app)
csrf   = CSRFProtect(app)
limiter = Limiter(get_remote_address, app=app)

CAIRO = pytz.timezone('Africa/Cairo')

# ─────────────────────────────────────────────
#  SECURITY HEADERS
# ─────────────────────────────────────────────
@app.after_request
def security_headers(response):
    response.headers['X-Frame-Options']           = 'DENY'
    response.headers['X-Content-Type-Options']    = 'nosniff'
    response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    return response


# ─────────────────────────────────────────────
#  MODELS
# ─────────────────────────────────────────────
class Booking(db.Model):
    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(100), nullable=False)
    age         = db.Column(db.Integer,     nullable=False)
    phone       = db.Column(db.String(20),  nullable=False)
    pain        = db.Column(db.String(300), nullable=False)
    conditions  = db.Column(db.String(300))
    date        = db.Column(db.String(20),  nullable=False)
    appointment = db.Column(db.String(20),  nullable=False)
    status      = db.Column(db.String(20),  default='confirmed')   # confirmed/cancelled
    cancel_token= db.Column(db.String(64),  unique=True)           # توكن الإلغاء
    created_at  = db.Column(db.DateTime,    default=datetime.utcnow)
    reminder_sent = db.Column(db.Boolean,   default=False)

    __table_args__ = (
        UniqueConstraint('date', 'appointment', name='uq_date_appointment'),
    )
    # العلاقة بملاحظات الجلسة
    session_notes = db.relationship('SessionNote', backref='booking', lazy=True)
    # العلاقة بالتقييم
    rating = db.relationship('BookingRating', backref='booking', uselist=False)


class PatientProfile(db.Model):
    """ملف المريض — يُنشأ تلقائياً أول حجز"""
    id           = db.Column(db.Integer, primary_key=True)
    name         = db.Column(db.String(100), nullable=False)
    phone        = db.Column(db.String(20),  nullable=False, unique=True, index=True)
    age          = db.Column(db.Integer)
    conditions   = db.Column(db.String(500))
    first_visit  = db.Column(db.String(20))
    last_visit   = db.Column(db.String(20))
    total_visits = db.Column(db.Integer, default=0)
    doctor_notes = db.Column(db.Text)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at   = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    session_notes = db.relationship('SessionNote', backref='patient',
                                    lazy=True, order_by='SessionNote.date.desc()')


class SessionNote(db.Model):
    """ملاحظة الدكتور على كل جلسة"""
    id           = db.Column(db.Integer, primary_key=True)
    patient_id   = db.Column(db.Integer, db.ForeignKey('patient_profile.id'), nullable=False)
    booking_id   = db.Column(db.Integer, db.ForeignKey('booking.id'))
    date         = db.Column(db.String(20), nullable=False)
    appointment  = db.Column(db.String(20))
    complaint    = db.Column(db.String(300))
    diagnosis    = db.Column(db.Text)
    treatment    = db.Column(db.Text)
    progress     = db.Column(db.String(50))   # ممتاز/جيد/لا تحسن/تراجع
    next_session = db.Column(db.String(300))
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)


class BookingRating(db.Model):
    """تقييم المريض بعد الجلسة"""
    id         = db.Column(db.Integer, primary_key=True)
    booking_id = db.Column(db.Integer, db.ForeignKey('booking.id'), nullable=False, unique=True)
    stars      = db.Column(db.Integer, nullable=False)   # 1-5
    comment    = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


with app.app_context():
    db.create_all()


# ─────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────
ALL_SLOTS = [f"{h}:{m} PM" for h in range(3, 11) for m in ('00', '30')]


def egypt_today():
    return datetime.now(CAIRO).date()


def valid_date(date_str):
    try:
        d = datetime.strptime(date_str, '%Y-%m-%d').date()
        if d.weekday() == 4:
            return False, "العيادة مغلقة يوم الجمعة"
        if d < egypt_today():
            return False, "لا يمكن الحجز في تاريخ سابق"
        return True, d
    except ValueError:
        return False, "تاريخ غير صالح"


def booked_slots(date):
    return [b.appointment for b in
            Booking.query.filter_by(date=date, status='confirmed').all()]


def upsert_patient(booking):
    p = PatientProfile.query.filter_by(phone=booking.phone).first()
    if p:
        p.last_visit   = booking.date
        p.total_visits = (p.total_visits or 0) + 1
        p.age          = booking.age
        if booking.conditions:
            old = {c.strip() for c in (p.conditions or '').split(',') if c.strip()}
            new = {c.strip() for c in booking.conditions.split(',')   if c.strip()}
            p.conditions = ', '.join(old | new)
        p.updated_at = datetime.utcnow()
    else:
        p = PatientProfile(
            name=booking.name, phone=booking.phone, age=booking.age,
            conditions=booking.conditions,
            first_visit=booking.date, last_visit=booking.date, total_visits=1)
        db.session.add(p)
    db.session.flush()
    return p


def admin_required(f):
    @wraps(f)
    def dec(*a, **kw):
        if not session.get('admin_logged_in'):
            return redirect('/login')
        return f(*a, **kw)
    return dec


# ─────────────────────────────────────────────
#  FORMS
# ─────────────────────────────────────────────
class BookingForm(FlaskForm):
    name  = StringField('الاسم', validators=[
        DataRequired(), Length(min=3),
        Regexp(r'^[أ-يa-zA-Z\s]+$', message="حروف فقط")])
    age   = IntegerField('العمر', validators=[DataRequired(), NumberRange(1, 120)])
    phone = TelField('الهاتف',   validators=[
        DataRequired(), Regexp(r'^\d{10,}$', message="10 أرقام على الأقل")])
    pain  = StringField('الشكوى', validators=[
        DataRequired(), Length(max=300),
        Regexp(r'^[^<>"\']+$', message="رموز غير مسموح بها")])
    date        = DateField('التاريخ', validators=[DataRequired()])
    appointment = SelectField('الميعاد', validators=[DataRequired()], choices=[])
    submit      = SubmitField('احجز الآن')


class LoginForm(FlaskForm):
    username = StringField('اسم المستخدم', validators=[DataRequired()])
    password = PasswordField('كلمة السر',  validators=[DataRequired()])
    submit   = SubmitField('دخول')


class SessionNoteForm(FlaskForm):
    complaint    = StringField('الشكوى',          validators=[Optional(), Length(max=300)])
    diagnosis    = TextAreaField('التشخيص',        validators=[Optional(), Length(max=1000)])
    treatment    = TextAreaField('العلاج المُعطى', validators=[Optional(), Length(max=1000)])
    progress     = SelectField('مستوى التحسن', choices=[
        ('', '— اختر —'), ('ممتاز', '✅ ممتاز'), ('جيد', '👍 جيد'),
        ('لا تحسن', '➖ لا تحسن'), ('تراجع', '⚠️ تراجع')],
        validators=[Optional()])
    next_session = StringField('توصيات الجلسة القادمة', validators=[Optional(), Length(max=300)])
    submit       = SubmitField('حفظ الملاحظة')


class DoctorNotesForm(FlaskForm):
    doctor_notes = TextAreaField('ملاحظات عامة', validators=[Optional(), Length(max=2000)])
    submit       = SubmitField('حفظ')


# ─────────────────────────────────────────────
#  WHATSAPP  (Twilio / CallMeBot)
# ─────────────────────────────────────────────
def send_whatsapp(phone, message):
    """إرسال واتساب عبر CallMeBot (مجاني) أو Twilio"""
    try:
        api_key = os.getenv("CALLMEBOT_API_KEY")
        if not api_key:
            app.logger.info("WhatsApp: CALLMEBOT_API_KEY not set, skipping")
            return
        import urllib.request, urllib.parse
        url = (f"https://api.callmebot.com/whatsapp.php"
               f"?phone={phone}&text={urllib.parse.quote(message)}&apikey={api_key}")
        urllib.request.urlopen(url, timeout=10)
    except Exception as e:
        app.logger.error(f"WhatsApp error: {e}")


def get_doctor_phone():
    """رقم الدكتور من الـ environment"""
    return os.getenv("DOCTOR_PHONE", "")


def notify_booking(name, phone, date, appointment):
    """إشعار واتساب للدكتور عند حجز جديد"""
    doctor = get_doctor_phone()
    if not doctor:
        app.logger.info("DOCTOR_PHONE not set, skipping WhatsApp")
        return
    msg = (f"🏥 حجز جديد - مركز الهادي\n"
           f"👤 الاسم: {name}\n"
           f"📱 الهاتف: {phone}\n"
           f"📅 التاريخ: {date}\n"
           f"🕐 الميعاد: {appointment}")
    threading.Thread(target=send_whatsapp, args=(doctor, msg), daemon=True).start()


def notify_reminder(name, phone, date, appointment):
    """تذكير للدكتور قبل الموعد بـ 24 ساعة"""
    doctor = get_doctor_phone()
    if not doctor:
        return
    msg = (f"⏰ تذكير بموعد غد - مركز الهادي\n"
           f"👤 {name}\n"
           f"📱 {phone}\n"
           f"📅 {date}\n"
           f"🕐 {appointment}")
    threading.Thread(target=send_whatsapp, args=(doctor, msg), daemon=True).start()


# ─────────────────────────────────────────────
#  AUTO REMINDER — بيشتغل في background
# ─────────────────────────────────────────────
def reminder_worker():
    """بيشتغل كل ساعة ويبعت تذكير للمرضى اللي موعدهم بكره"""
    import time
    while True:
        time.sleep(3600)   # كل ساعة
        try:
            with app.app_context():
                tomorrow = (egypt_today() + timedelta(days=1)).strftime('%Y-%m-%d')
                pending  = Booking.query.filter_by(
                    date=tomorrow, status='confirmed', reminder_sent=False).all()
                for b in pending:
                    notify_reminder(b.name, b.phone, b.date, b.appointment)
                    b.reminder_sent = True
                db.session.commit()
                if pending:
                    app.logger.info(f"Reminders sent: {len(pending)}")
        except Exception as e:
            app.logger.error(f"Reminder worker error: {e}")


# تشغيل الـ reminder في الخلفية عند بدء التطبيق
threading.Thread(target=reminder_worker, daemon=True).start()





# ─────────────────────────────────────────────
#  PUBLIC ROUTES
# ─────────────────────────────────────────────
@app.route('/')
def index():
    today = egypt_today().strftime('%Y-%m-%d')
    date  = request.args.get('date', today)
    ok, _ = valid_date(date)
    if not ok:
        date = today

    free  = [s for s in ALL_SLOTS if s not in booked_slots(date)]
    form  = BookingForm()
    form.appointment.choices = [(t, t) for t in free] if free else [('', 'لا توجد مواعيد')]
    form.date.data = datetime.strptime(date, '%Y-%m-%d')
    return render_template('index.html', form=form, available_times=free, selected_date=date)


@app.route('/available_slots')
def available_slots():
    date = request.args.get('date')
    if not date:
        return jsonify({'available_times': [], 'error': 'التاريخ مطلوب'})
    ok, result = valid_date(date)
    if not ok:
        return jsonify({'available_times': [], 'error': result})
    return jsonify({'available_times': [s for s in ALL_SLOTS if s not in booked_slots(date)]})


@app.route('/submit', methods=['POST'])
@limiter.limit("5 per minute")
def submit():
    name        = request.form.get('name', '').strip()
    phone       = request.form.get('phone', '').strip()
    pain        = request.form.get('pain', '').strip()
    date_str    = request.form.get('date', '').strip()
    appointment = request.form.get('appointment', '').strip()
    conditions  = request.form.getlist('conditions')
    age_str     = request.form.get('age', '').strip()

    errors = []
    age = 0
    if not name or len(name) < 3:          errors.append("الاسم يجب أن يكون 3 أحرف على الأقل")
    if not phone.isdigit() or len(phone)<10: errors.append("رقم الهاتف غير صالح")
    if not pain:                            errors.append("يرجى وصف الألم")
    try:
        age = int(age_str)
        if not (1 <= age <= 120):          errors.append("العمر غير منطقي")
    except:                                 errors.append("العمر غير صالح")
    if not date_str:                        errors.append("التاريخ مطلوب")
    else:
        ok, res = valid_date(date_str)
        if not ok:                         errors.append(res)
    if not appointment:                    errors.append("يرجى اختيار ميعاد")
    elif date_str and appointment in booked_slots(date_str):
                                           errors.append("هذا الموعد محجوز بالفعل")
    if errors:
        for e in errors: flash(e, 'error')
        return redirect('/')

    token = secrets.token_urlsafe(32)
    try:
        b = Booking(
            name=html.escape(name), age=age, phone=phone,
            pain=html.escape(pain), conditions=', '.join(conditions),
            date=date_str, appointment=appointment, cancel_token=token)
        db.session.add(b)
        db.session.flush()
        upsert_patient(b)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("هذا الموعد محجوز بالفعل.", 'error')
        return redirect('/')
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"DB error: {e}")
        flash("حدث خطأ، يرجى المحاولة مرة أخرى.", 'error')
        return redirect('/')

    # إشعارات
    notify_booking(name, phone, date_str, appointment)


    return redirect(f'/confirmation?token={token}')


@app.route('/confirmation')
def confirmation():
    token = request.args.get('token', '')
    b = Booking.query.filter_by(cancel_token=token).first()
    return render_template('confirmation.html', booking=b, token=token)


# ─────────────────────────────────────────────
#  CANCEL / EDIT BOOKING  (بدون login)
# ─────────────────────────────────────────────
@app.route('/cancel/<token>')
def cancel_booking_page(token):
    b = Booking.query.filter_by(cancel_token=token).first_or_404()
    return render_template('cancel_booking.html', booking=b)


@app.route('/cancel/<token>/confirm', methods=['POST'])
@limiter.limit("3 per minute")
def cancel_booking_confirm(token):
    b = Booking.query.filter_by(cancel_token=token).first_or_404()
    if b.status == 'cancelled':
        flash("هذا الحجز ملغى بالفعل", 'error')
        return redirect('/')
    b.status = 'cancelled'
    db.session.commit()
    # إشعار واتساب بالإلغاء
    msg = f"❌ تم إلغاء حجزك في مركز الهادي\nالتاريخ: {b.date} — {b.appointment}\nللحجز مرة أخرى زور الموقع 💚"
    threading.Thread(target=send_whatsapp, args=(b.phone, msg), daemon=True).start()
    return render_template('cancel_success.html', booking=b)


# ─────────────────────────────────────────────
#  RATING SYSTEM
# ─────────────────────────────────────────────
@app.route('/rate/<token>', methods=['GET', 'POST'])
@limiter.limit("3 per minute")
def rate_booking(token):
    b = Booking.query.filter_by(cancel_token=token).first_or_404()
    if b.rating:
        return render_template('rate.html', booking=b, already_rated=True)
    if request.method == 'POST':
        stars   = int(request.form.get('stars', 0))
        comment = html.escape(request.form.get('comment', '').strip()[:500])
        if not (1 <= stars <= 5):
            flash("يرجى اختيار تقييم من 1 إلى 5", 'error')
            return redirect(f'/rate/{token}')
        r = BookingRating(booking_id=b.id, stars=stars, comment=comment)
        db.session.add(r)
        db.session.commit()
        return render_template('rate.html', booking=b, done=True)
    return render_template('rate.html', booking=b)



# ─────────────────────────────────────────────
#  RATE — صفحة تقييم مستقلة (بالهاتف)
# ─────────────────────────────────────────────
@app.route('/rate', methods=['GET', 'POST'])
@limiter.limit("5 per minute")
def rate_page():
    """صفحة تقييم مستقلة — المريض يدخل رقمه ويقيّم"""
    done = False
    already_rated = False
    booking = None
    error = None

    if request.method == 'POST':
        phone   = request.form.get('phone', '').strip()
        stars   = request.form.get('stars', '0')
        comment = html.escape(request.form.get('comment', '').strip()[:500])

        if not phone.isdigit() or len(phone) < 10:
            error = "يرجى إدخال رقم هاتف صحيح"
        else:
            # آخر حجز مؤكد لهذا الرقم
            booking = (Booking.query
                       .filter_by(phone=phone, status='confirmed')
                       .order_by(Booking.date.desc())
                       .first())
            if not booking:
                error = "لم نجد حجزاً مرتبطاً بهذا الرقم"
            elif booking.rating:
                already_rated = True
            else:
                try:
                    stars = int(stars)
                except:
                    stars = 0
                if not (1 <= stars <= 5):
                    error = "يرجى اختيار تقييم من 1 إلى 5 نجوم"
                else:
                    r = BookingRating(booking_id=booking.id,
                                      stars=stars, comment=comment)
                    db.session.add(r)
                    db.session.commit()
                    done = True

    return render_template('rate_page.html',
                           done=done, already_rated=already_rated,
                           booking=booking, error=error)

# ─────────────────────────────────────────────
#  ADMIN AUTH
# ─────────────────────────────────────────────
@app.route('/login', methods=['GET', 'POST'])
@limiter.limit("3 per minute")
def login():
    form = LoginForm()
    error = None
    if form.validate_on_submit():
        if (form.username.data == os.getenv("ADMIN_USERNAME") and
                check_password_hash(os.getenv("ADMIN_PASSWORD", ""), form.password.data)):
            session['admin_logged_in'] = True
            return redirect('/dashboard')
        error = "بيانات الدخول غير صحيحة"
    return render_template('login.html', form=form, error=error)


@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')


# ─────────────────────────────────────────────
#  ADMIN — DASHBOARD
# ─────────────────────────────────────────────
@app.route('/dashboard')
@admin_required
def dashboard():
    today      = egypt_today().strftime('%Y-%m-%d')
    total      = Booking.query.filter_by(status='confirmed').count()
    today_count= Booking.query.filter_by(date=today, status='confirmed').count()
    patients_n = PatientProfile.query.count()
    cancelled  = Booking.query.filter_by(status='cancelled').count()

    # آخر 7 أيام — حجوزات يومية
    days_data = []
    for i in range(6, -1, -1):
        d    = (egypt_today() - timedelta(days=i)).strftime('%Y-%m-%d')
        cnt  = Booking.query.filter_by(date=d, status='confirmed').count()
        days_data.append({'date': d, 'count': cnt})

    # توزيع الحالات
    from sqlalchemy import func
    conditions_raw = db.session.query(Booking.conditions).filter(
        Booking.conditions != '', Booking.conditions != None).all()
    cond_count = {}
    for (c,) in conditions_raw:
        for item in c.split(','):
            item = item.strip()
            if item:
                cond_count[item] = cond_count.get(item, 0) + 1

    # متوسط التقييم
    ratings = BookingRating.query.all()
    avg_rating = round(sum(r.stars for r in ratings) / len(ratings), 1) if ratings else 0

    # أحدث 5 حجوزات
    recent = Booking.query.filter_by(status='confirmed')\
                          .order_by(Booking.created_at.desc()).limit(5).all()

    return render_template('dashboard.html',
        total=total, today_count=today_count,
        patients_n=patients_n, cancelled=cancelled,
        days_data=days_data, cond_count=cond_count,
        avg_rating=avg_rating, ratings_count=len(ratings),
        recent=recent)


# ─────────────────────────────────────────────
#  ADMIN — BOOKINGS + CALENDAR
# ─────────────────────────────────────────────
@app.route('/bookings')
@admin_required
def bookings():
    search      = request.args.get('search', '').strip()
    date_filter = request.args.get('date_filter', '').strip()
    view        = request.args.get('view', 'table')   # table | calendar
    query = Booking.query
    if search:
        query = query.filter(db.or_(
            Booking.name.ilike(f'%{search}%'),
            Booking.phone.ilike(f'%{search}%')))
    if date_filter:
        query = query.filter_by(date=date_filter)
    all_bookings = query.order_by(Booking.date, Booking.appointment).all()

    # بيانات Calendar — حجوزات الشهر الحالي
    cal_data = {}
    if view == 'calendar':
        today = egypt_today()
        first = today.replace(day=1)
        last  = (first.replace(month=first.month % 12 + 1, day=1)
                 if first.month < 12 else first.replace(year=first.year+1, month=1, day=1))
        month_bookings = Booking.query.filter(
            Booking.date >= first.strftime('%Y-%m-%d'),
            Booking.date <  last.strftime('%Y-%m-%d'),
            Booking.status == 'confirmed').all()
        for b in month_bookings:
            cal_data.setdefault(b.date, []).append(b)

    return render_template('bookings.html',
        bookings=all_bookings, search=search,
        date_filter=date_filter, view=view,
        cal_data=cal_data, today=egypt_today().strftime('%Y-%m-%d'))


@app.route('/delete_booking/<int:bid>', methods=['POST'])
@admin_required
def delete_booking(bid):
    b = Booking.query.get_or_404(bid)
    db.session.delete(b)
    db.session.commit()
    flash("تم حذف الحجز", 'success')
    return redirect('/bookings')


# ─────────────────────────────────────────────
#  ADMIN — PATIENTS + PROFILE + NOTES
# ─────────────────────────────────────────────
@app.route('/patients')
@admin_required
def patients():
    search = request.args.get('search', '').strip()
    query  = PatientProfile.query
    if search:
        query = query.filter(db.or_(
            PatientProfile.name.ilike(f'%{search}%'),
            PatientProfile.phone.ilike(f'%{search}%')))
    all_patients = query.order_by(PatientProfile.last_visit.desc()).all()
    return render_template('patients.html', patients=all_patients, search=search)


@app.route('/patient/<int:pid>')
@admin_required
def patient_profile(pid):
    p        = PatientProfile.query.get_or_404(pid)
    history  = Booking.query.filter_by(phone=p.phone)\
                            .order_by(Booking.date.desc()).all()
    nf       = SessionNoteForm()
    df       = DoctorNotesForm(doctor_notes=p.doctor_notes)
    return render_template('patient_profile.html',
        patient=p, bookings_history=history, note_form=nf, doctor_form=df)


@app.route('/patient/<int:pid>/add_note', methods=['POST'])
@admin_required
def add_session_note(pid):
    p  = PatientProfile.query.get_or_404(pid)
    nf = SessionNoteForm()
    if nf.validate_on_submit():
        note = SessionNote(
            patient_id   = p.id,
            booking_id   = request.form.get('booking_id') or None,
            date         = request.form.get('note_date') or egypt_today().strftime('%Y-%m-%d'),
            appointment  = request.form.get('note_appointment', ''),
            complaint    = html.escape(nf.complaint.data or ''),
            diagnosis    = html.escape(nf.diagnosis.data or ''),
            treatment    = html.escape(nf.treatment.data or ''),
            progress     = nf.progress.data or '',
            next_session = html.escape(nf.next_session.data or ''))
        db.session.add(note)
        db.session.commit()
        flash("✅ تم حفظ ملاحظة الجلسة", 'success')
    return redirect(f'/patient/{pid}')


@app.route('/patient/<int:pid>/update_notes', methods=['POST'])
@admin_required
def update_doctor_notes(pid):
    p  = PatientProfile.query.get_or_404(pid)
    df = DoctorNotesForm()
    if df.validate_on_submit():
        p.doctor_notes = html.escape(df.doctor_notes.data or '')
        p.updated_at   = datetime.utcnow()
        db.session.commit()
        flash("✅ تم حفظ الملاحظات", 'success')
    return redirect(f'/patient/{pid}')


@app.route('/patient/<int:pid>/delete_note/<int:nid>', methods=['POST'])
@admin_required
def delete_session_note(pid, nid):
    n = SessionNote.query.get_or_404(nid)
    db.session.delete(n)
    db.session.commit()
    flash("تم حذف الملاحظة", 'success')
    return redirect(f'/patient/{pid}')


@app.route('/patient/<int:pid>/delete', methods=['POST'])
@admin_required
def delete_patient(pid):
    SessionNote.query.filter_by(patient_id=pid).delete()
    PatientProfile.query.filter_by(id=pid).delete()
    db.session.commit()
    flash("تم حذف ملف المريض", 'success')
    return redirect('/patients')


# ─────────────────────────────────────────────
#  CSV EXPORT
# ─────────────────────────────────────────────
@app.route('/export/bookings')
@admin_required
def export_bookings():
    output = io.StringIO()
    w = csv.writer(output)
    w.writerow(['#', 'الاسم', 'العمر', 'الهاتف', 'الشكوى',
                'الحالات', 'التاريخ', 'الميعاد', 'الحالة', 'تاريخ الحجز'])
    for i, b in enumerate(Booking.query.order_by(Booking.date).all(), 1):
        w.writerow([i, b.name, b.age, b.phone, b.pain,
                    b.conditions, b.date, b.appointment,
                    b.status, b.created_at.strftime('%Y-%m-%d %H:%M') if b.created_at else ''])
    output.seek(0)
    bom = '\ufeff' + output.getvalue()
    return send_file(
        io.BytesIO(bom.encode('utf-8')),
        mimetype='text/csv; charset=utf-8',
        as_attachment=True,
        download_name=f"bookings_{egypt_today()}.csv")


@app.route('/export/patients')
@admin_required
def export_patients():
    output = io.StringIO()
    w = csv.writer(output)
    w.writerow(['#', 'الاسم', 'الهاتف', 'العمر', 'الحالات المزمنة',
                'أول زيارة', 'آخر زيارة', 'إجمالي الزيارات', 'ملاحظات الدكتور'])
    for i, p in enumerate(PatientProfile.query.order_by(PatientProfile.name).all(), 1):
        w.writerow([i, p.name, p.phone, p.age, p.conditions,
                    p.first_visit, p.last_visit, p.total_visits,
                    (p.doctor_notes or '').replace('\n', ' ')])
    output.seek(0)
    bom = '\ufeff' + output.getvalue()
    return send_file(
        io.BytesIO(bom.encode('utf-8')),
        mimetype='text/csv; charset=utf-8',
        as_attachment=True,
        download_name=f"patients_{egypt_today()}.csv")


if __name__ == '__main__':
    app.run(debug=False)

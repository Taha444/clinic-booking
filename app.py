import os
import threading
import html
from flask import Flask, render_template, request, redirect, session, jsonify, flash
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import FlaskForm, CSRFProtect
from wtforms import StringField, IntegerField, TelField, DateField, SelectField, SubmitField, PasswordField
from wtforms.validators import DataRequired, NumberRange, Length, Regexp
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


class Booking(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    age = db.Column(db.Integer, nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    pain = db.Column(db.String(200), nullable=False)
    conditions = db.Column(db.String(200), nullable=True)
    date = db.Column(db.String(20), nullable=False)
    appointment = db.Column(db.String(20), nullable=False)

    __table_args__ = (
        UniqueConstraint('date', 'appointment', name='uq_date_appointment'),
    )

with app.app_context():
    db.create_all()

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
    bookings = Booking.query.filter_by(date=date).all()
    return [b.appointment for b in bookings]


class BookingForm(FlaskForm):
    name = StringField('الاسم', validators=[
        DataRequired(), Length(min=3),
        Regexp(r'^[أ-يa-zA-Z\s]+$', message="حروف فقط")
    ])
    age = IntegerField('العمر', validators=[DataRequired(), NumberRange(min=1, max=120)])
    phone = TelField('رقم الهاتف', validators=[
        DataRequired(), Regexp(r'^\d{10,}$', message="10 أرقام على الأقل")
    ])
    pain = StringField('بماذا تشعر؟', validators=[
        DataRequired(), Length(max=200),
        Regexp(r'^[^<>"\']+$', message="رموز غير مسموح بها")
    ])
    date = DateField('تاريخ الحجز', validators=[DataRequired()])
    appointment = SelectField('ميعاد الحجز', validators=[DataRequired()], choices=[])
    submit = SubmitField('احجز الآن')


class LoginForm(FlaskForm):
    username = StringField('اسم المستخدم', validators=[DataRequired()])
    password = PasswordField('كلمة السر', validators=[DataRequired()])
    submit = SubmitField('تسجيل الدخول')


@app.route('/')
def index():
    today_str = get_egypt_today().strftime('%Y-%m-%d')
    date = request.args.get('date', today_str)

    valid, _ = is_valid_booking_date(date)
    if not valid:
        date = today_str

    booked = get_booked_slots(date)
    available_times = [s for s in all_slots if s not in booked]

    form = BookingForm()
    form.appointment.choices = [(t, t) for t in available_times] if available_times else [('', 'لا توجد مواعيد')]
    form.date.data = datetime.strptime(date, '%Y-%m-%d')

    return render_template('index.html', form=form, available_times=available_times, selected_date=date)


@app.route('/available_slots')
def available_slots():
    date = request.args.get('date')
    if not date:
        return jsonify({'available_times': [], 'error': 'التاريخ مطلوب'})
    valid, result = is_valid_booking_date(date)
    if not valid:
        return jsonify({'available_times': [], 'error': result})
    booked = get_booked_slots(date)
    available_times = [s for s in all_slots if s not in booked]
    return jsonify({'available_times': available_times})


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

    # ── Validation ──
    errors = []

    if not name or len(name) < 3:
        errors.append("الاسم يجب أن يكون 3 أحرف على الأقل")

    try:
        age = int(age_str)
        if age < 1 or age > 120:
            errors.append("العمر غير منطقي")
    except (ValueError, TypeError):
        errors.append("العمر غير صالح")
        age = 0

    if not phone.isdigit() or len(phone) < 10:
        errors.append("رقم الهاتف يجب أن يحتوي على 10 أرقام على الأقل")

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
        errors.append("هذا الموعد محجوز بالفعل، يرجى اختيار وقت آخر")

    if errors:
        for e in errors:
            flash(e, 'error')
        return redirect('/')

    # ── حفظ في DB ──
    try:
        new_booking = Booking(
            name=html.escape(name),
            age=age,
            phone=phone,
            pain=html.escape(pain),
            conditions=', '.join(conditions),
            date=date_str,
            appointment=appointment
        )
        db.session.add(new_booking)
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

    # ── إرسال إيميل في background (عشان مايحرقش الـ worker) ──
    message = f"""حجز جديد - مركز الهادي

الاسم: {name}
العمر: {age}
الهاتف: {phone}
التاريخ: {date_str}
الميعاد: {appointment}
الوصف: {pain}
الحالات: {', '.join(conditions) or 'لا يوجد'}"""
    t = threading.Thread(target=send_email_safe,
                         args=("tetoelsalahy@gmail.com", "حجز جديد - عيادة الهادي", message),
                         daemon=True)
    t.start()

    return redirect('/confirmation')


@app.route('/confirmation')
def confirmation():
    return render_template('confirmation.html')


@app.route('/login', methods=['GET', 'POST'])
@limiter.limit("3 per minute")
def login():
    form = LoginForm()
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


@app.route('/bookings')
def bookings():
    if not session.get('admin_logged_in'):
        return redirect('/login')
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
def delete_booking(booking_id):
    if not session.get('admin_logged_in'):
        return redirect('/login')
    booking = Booking.query.get_or_404(booking_id)
    db.session.delete(booking)
    db.session.commit()
    flash("تم حذف الحجز بنجاح", 'success')
    return redirect('/bookings')


def send_email_safe(to, subject, body):
    """بتشتغل في background thread — مش بتأثر على الـ request"""
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

import os
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
    # ✅ إضافة Content Security Policy
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "script-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:;"
    )
    return response


# ✅ تصليح: إضافة UniqueConstraint لمنع Race Condition في الحجز
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
    """إرجاع تاريخ اليوم بتوقيت القاهرة"""
    return datetime.now(pytz.timezone('Africa/Cairo')).date()


def is_valid_booking_date(date_str):
    """التحقق من صحة تاريخ الحجز"""
    try:
        selected_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        today = get_egypt_today()
        # ✅ تصليح: weekday()==4 هو الجمعة في Python — صحيح
        if selected_date.weekday() == 4:
            return False, "العيادة مغلقة يوم الجمعة"
        if selected_date < today:
            return False, "لا يمكن الحجز في تاريخ سابق"
        return True, selected_date
    except ValueError:
        return False, "تاريخ غير صالح"


class BookingForm(FlaskForm):
    name = StringField('الاسم', validators=[
        DataRequired(message="الاسم مطلوب"),
        Length(min=3, message="الاسم يجب أن يكون 3 أحرف على الأقل"),
        Regexp(r'^[أ-يa-zA-Z\s]+$', message="الاسم يجب أن يحتوي على حروف فقط")
    ])
    age = IntegerField('العمر', validators=[
        DataRequired(message="العمر مطلوب"),
        NumberRange(min=1, max=120, message="العمر يجب أن يكون بين 1 و 120")
    ])
    phone = TelField('رقم الهاتف', validators=[
        DataRequired(message="رقم الهاتف مطلوب"),
        Regexp(r'^\d{10,}$', message="رقم الهاتف يجب أن يحتوي على 10 أرقام على الأقل")
    ])
    pain = StringField('بماذا تشعر؟', validators=[
        DataRequired(message="يرجى وصف الألم"),
        Length(max=200),
        Regexp(r'^[^<>"\']+$', message="المدخل يحتوي على رموز غير مسموح بها")
    ])
    date = DateField('تاريخ الحجز', validators=[DataRequired(message="التاريخ مطلوب")])
    appointment = SelectField('ميعاد الحجز', validators=[DataRequired(message="يرجى اختيار ميعاد")])
    submit = SubmitField('احجز الآن')


class LoginForm(FlaskForm):
    username = StringField('اسم المستخدم', validators=[DataRequired()])
    password = PasswordField('كلمة السر', validators=[DataRequired()])
    submit = SubmitField('تسجيل الدخول')


def get_booked_slots(date):
    bookings = Booking.query.filter_by(date=date).all()
    return [b.appointment for b in bookings]


@app.route('/')
def index():
    today_str = get_egypt_today().strftime('%Y-%m-%d')
    date = request.args.get('date', today_str)

    # التحقق من صحة التاريخ المطلوب
    valid, result = is_valid_booking_date(date)
    if not valid:
        date = today_str

    booked = get_booked_slots(date)
    available_times = [slot for slot in all_slots if slot not in booked]

    form = BookingForm()
    form.appointment.choices = [(time, time) for time in available_times] if available_times else [('', 'لا توجد مواعيد متاحة')]
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
    available_times = [slot for slot in all_slots if slot not in booked]
    return jsonify({'available_times': available_times})


# ✅ تصليح رئيسي: استخدام WTForms validate_on_submit بدل قراءة request.form مباشرة
@app.route('/submit', methods=['GET', 'POST'])
@limiter.limit("5 per minute")
def submit():
    today_str = get_egypt_today().strftime('%Y-%m-%d')
    date_param = request.args.get('date', today_str)

    booked = get_booked_slots(date_param)
    available_times = [slot for slot in all_slots if slot not in booked]

    form = BookingForm()
    form.appointment.choices = [(time, time) for time in available_times] if available_times else [('', 'لا توجد مواعيد')]

    if form.validate_on_submit():
        date_str = form.date.data.strftime('%Y-%m-%d')

        # التحقق من صحة التاريخ مرة أخرى في الـ backend
        valid, result = is_valid_booking_date(date_str)
        if not valid:
            flash(result, 'error')
            return render_template('index.html', form=form, available_times=available_times, selected_date=date_str)

        appointment = form.appointment.data

        # ✅ تصليح Race Condition: استخدام DB transaction مع exception handling
        try:
            new_booking = Booking(
                name=html.escape(form.name.data.strip()),
                age=form.age.data,
                phone=form.phone.data.strip(),
                pain=html.escape(form.pain.data.strip()),
                conditions=', '.join(request.form.getlist('conditions')),
                date=date_str,
                appointment=appointment
            )
            db.session.add(new_booking)
            db.session.commit()
        except Exception:
            db.session.rollback()
            flash("هذا الموعد محجوز بالفعل، يرجى اختيار وقت آخر.", 'error')
            return render_template('index.html', form=form, available_times=available_times, selected_date=date_str)

        # إرسال الإيميل بشكل آمن (لو فشل ما يوقفش الحجز)
        try:
            message = f"""✅ حجز جديد - مركز الهادي للعلاج الطبيعي

الاسم: {form.name.data}
العمر: {form.age.data}
الهاتف: {form.phone.data}
التاريخ: {date_str}
الوصف: {form.pain.data}
الحالات: {', '.join(request.form.getlist('conditions')) or 'لا يوجد'}
الميعاد: {appointment}"""
            send_email("tetoelsalahy@gmail.com", "✅ حجز جديد - عيادة الهادي", message)
        except Exception as e:
            app.logger.error(f"Email sending failed: {e}")

        return redirect('/confirmation')

    # إذا كان GET أو validation فشل — ارجع للصفحة الرئيسية
    return redirect('/')


@app.route('/confirmation')
def confirmation():
    return render_template('confirmation.html')


@app.route('/login', methods=['GET', 'POST'])
@limiter.limit("3 per minute")
def login():
    form = LoginForm()
    error = None
    env_username = os.getenv("ADMIN_USERNAME")
    env_password_hash = os.getenv("ADMIN_PASSWORD")

    if form.validate_on_submit():
        if form.username.data == env_username and check_password_hash(env_password_hash, form.password.data):
            session['admin_logged_in'] = True
            session.permanent = False  # session تنتهي لما يقفل المتصفح
            return redirect('/bookings')
        else:
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

    # دعم البحث والفلترة
    search = request.args.get('search', '').strip()
    date_filter = request.args.get('date_filter', '').strip()

    query = Booking.query
    if search:
        query = query.filter(
            db.or_(
                Booking.name.ilike(f'%{search}%'),
                Booking.phone.ilike(f'%{search}%')
            )
        )
    if date_filter:
        query = query.filter_by(date=date_filter)

    all_bookings = query.order_by(Booking.date, Booking.appointment).all()
    return render_template('bookings.html', bookings=all_bookings, search=search, date_filter=date_filter)


@app.route('/delete_booking/<int:booking_id>', methods=['POST'])
def delete_booking(booking_id):
    if not session.get('admin_logged_in'):
        return redirect('/login')
    booking = Booking.query.get_or_404(booking_id)
    db.session.delete(booking)
    db.session.commit()
    flash("تم حذف الحجز بنجاح", 'success')
    return redirect('/bookings')


def send_email(to, subject, body):
    sender = os.getenv("EMAIL_SENDER", "elhadyclinic1@gmail.com")
    password = os.getenv("EMAIL_PASSWORD")
    if not password:
        app.logger.warning("EMAIL_PASSWORD not set, skipping email")
        return
    msg = MIMEText(body, 'plain', 'utf-8')
    msg['Subject'] = subject
    msg['From'] = sender
    msg['To'] = to
    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
        server.login(sender, password)
        server.sendmail(sender, to, msg.as_string())


if __name__ == '__main__':
    app.run(debug=False)  # ✅ تصليح: debug=False في الـ production

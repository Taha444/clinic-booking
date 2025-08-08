import os
import html
from flask import Flask, render_template, request, redirect, session, jsonify
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

with app.app_context():
    db.create_all()

all_slots = []
for hour in range(3, 11):
    all_slots.append(f"{hour}:00 PM")
    all_slots.append(f"{hour}:30 PM")

class BookingForm(FlaskForm):
    name = StringField('الاسم', validators=[
        DataRequired(),
        Length(min=3),
        Regexp(r'^[أ-يa-zA-Z\s]+$', message="الاسم يجب أن يحتوي على حروف فقط")
    ])
    age = IntegerField('العمر', validators=[DataRequired(), NumberRange(min=1, max=120)])
    phone = TelField('رقم الهاتف', validators=[
        DataRequired(),
        Regexp(r'^\d{10,}$', message="رقم الهاتف يجب أن يحتوي على 10 أرقام على الأقل")
    ])
    pain = StringField('بماذا تشعر؟', validators=[
        DataRequired(),
        Regexp(r'^[^<>"]+$', message="المدخل يحتوي على رموز غير مسموح بها")
    ])
    date = DateField('تاريخ الحجز', validators=[DataRequired()])
    appointment = SelectField('ميعاد الحجز', validators=[DataRequired()])
    submit = SubmitField('احجز')

class LoginForm(FlaskForm):
    username = StringField('اسم المستخدم', validators=[DataRequired()])
    password = PasswordField('كلمة السر', validators=[DataRequired()])
    submit = SubmitField('تسجيل الدخول')

def get_booked_slots(date):
    bookings = Booking.query.filter_by(date=date).all()
    return [b.appointment for b in bookings]

@app.route('/')
def index():
    egypt_time = datetime.now(pytz.timezone('Africa/Cairo'))
    today_str = egypt_time.strftime('%Y-%m-%d')
    date = request.args.get('date', today_str)
    booked = get_booked_slots(date)
    available_times = [slot for slot in all_slots if slot not in booked]
    form = BookingForm()
    form.appointment.choices = [(time, time) for time in available_times]
    form.date.data = datetime.strptime(date, '%Y-%m-%d')
    return render_template('index.html', form=form, available_times=available_times, selected_date=date)

@app.route('/available_slots')
def available_slots():
    date = request.args.get('date')
    if not date:
        return jsonify({'available_times': []})
    try:
        selected_date = datetime.strptime(date, '%Y-%m-%d')
        today = datetime.now(pytz.timezone('Africa/Cairo')).date()
        if selected_date.weekday() == 4 or selected_date.date() < today:
            return jsonify({'available_times': []})
    except ValueError:
        return jsonify({'available_times': []})
    booked = get_booked_slots(date)
    available_times = [slot for slot in all_slots if slot not in booked]
    return jsonify({'available_times': available_times})

@app.route('/submit', methods=['POST'])
@limiter.limit("5 per minute")
def submit():
    if not request.form:
        return "طلب غير صالح", 400

    name = request.form['name'].strip()
    age = request.form['age']
    phone = request.form['phone'].strip()
    date = request.form['date']
    pain = html.escape(request.form['pain'].strip())  # حماية من XSS
    conditions = request.form.getlist('conditions')
    appointment = request.form['appointment']

    try:
        age = int(age)
        if age < 1 or age > 120:
            return "العمر غير منطقي", 400
        if not name or len(name) < 3:
            return "الاسم غير صالح", 400
        if not phone.isdigit() or len(phone) < 10:
            return "رقم الهاتف غير صالح", 400
        selected_date = datetime.strptime(date, '%Y-%m-%d')
        today = datetime.now(pytz.timezone('Africa/Cairo')).date()
        if selected_date.weekday() == 4 or selected_date.date() < today:
            return "لا يمكن الحجز في يوم الجمعة أو في تاريخ سابق.", 400
    except ValueError:
        return "تاريخ أو بيانات غير صالحة", 400

    if appointment in get_booked_slots(date):
        return "هذا الموعد محجوز بالفعل، يرجى اختيار وقت آخر."

    new_booking = Booking(
        name=name,
        age=age,
        phone=phone,
        pain=pain,
        conditions=', '.join(conditions),
        date=date,
        appointment=appointment
    )
    db.session.add(new_booking)
    db.session.commit()

    message = f"""New Patient Booking:
Name: {name}
Age: {age}
Phone: {phone}
Date: {date}
Pain: {pain}
Conditions: {', '.join(conditions)}
Appointment Time: {appointment}"""
    send_email("tetoelsalahy@gmail.com", "New Patient Booking", message)
    return redirect('/confirmation')

@app.route('/confirmation')
def confirmation():
    return render_template('confirmation.html')

@app.route('/login', methods=['GET', 'POST'])
@limiter.limit("3 per minute")  # حماية من التخمين
def login():
    form = LoginForm()
    error = None
    env_username = os.getenv("ADMIN_USERNAME")
    env_password_hash = os.getenv("ADMIN_PASSWORD")
    if form.validate_on_submit():
        input_username = form.username.data
        input_password = form.password.data
        if input_username == env_username and check_password_hash(env_password_hash, input_password):
            session['admin_logged_in'] = True
            return redirect('/bookings')
        else:
            error = "بيانات الدخول غير صحيحة"
    return render_template('login.html', form=form, error=error)

@app.route('/logout')
def logout():
    session.pop('admin_logged_in', None)
    return redirect('/login')

@app.route('/bookings')
def bookings():
    if not session.get('admin_logged_in'):
        return redirect('/login')
    bookings = Booking.query.all()
    return render_template('bookings.html', bookings=bookings)

def send_email(to, subject, body):
    sender = os.getenv("EMAIL_SENDER", "elhadyclinic1@gmail.com")
    password = os.getenv("EMAIL_PASSWORD")
    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = sender
    msg['To'] = to
    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
        server.login(sender, password)
        server.sendmail(sender, to, msg.as_string())

if __name__ == '__main__':
    app.run(debug=True)

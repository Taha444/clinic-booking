import os
from flask import Flask, render_template, request, redirect, session, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import FlaskForm, CSRFProtect
from wtforms import StringField, IntegerField, TelField, DateField, SelectField, SubmitField, PasswordField
from wtforms.validators import DataRequired
from datetime import datetime
import pytz
from dotenv import load_dotenv
import smtplib
from email.mime.text import MIMEText
# تحميل متغيرات البيئة
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY")

# إعداد قاعدة البيانات باستخدام SQLAlchemy
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///clinic.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

csrf = CSRFProtect(app)

# نموذج قاعدة البيانات
class Booking(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    age = db.Column(db.Integer, nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    pain = db.Column(db.String(200), nullable=False)
    conditions = db.Column(db.String(200), nullable=True)
    date = db.Column(db.String(20), nullable=False)
    appointment = db.Column(db.String(20), nullable=False)

# إنشاء قاعدة البيانات
with app.app_context():
    db.create_all()

# كل المواعيد من 3:00 PM إلى 10:00 PM بنص ساعة
all_slots = []
for hour in range(3, 11):
    all_slots.append(f"{hour}:00 PM")
    all_slots.append(f"{hour}:30 PM")

# نموذج الحجز
class BookingForm(FlaskForm):
    name = StringField('الاسم', validators=[DataRequired()])
    age = IntegerField('العمر', validators=[DataRequired()])
    phone = TelField('رقم الهاتف', validators=[DataRequired()])
    pain = StringField('بماذا تشعر؟', validators=[DataRequired()])
    date = DateField('تاريخ الحجز', validators=[DataRequired()])
    appointment = SelectField('ميعاد الحجز', validators=[DataRequired()])
    submit = SubmitField('احجز')

# نموذج تسجيل الدخول
class LoginForm(FlaskForm):
    username = StringField('اسم المستخدم', validators=[DataRequired()])
    password = PasswordField('كلمة السر', validators=[DataRequired()])
    submit = SubmitField('تسجيل الدخول')

# استخراج المواعيد المحجوزة من قاعدة البيانات
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
    booked = get_booked_slots(date)
    available_times = [slot for slot in all_slots if slot not in booked]
    return jsonify({'available_times': available_times})

@app.route('/submit', methods=['POST'])
def submit():
    name = request.form['name']
    age = request.form['age']
    phone = request.form['phone']
    date = request.form['date']
    pain = request.form['pain']
    conditions = request.form.getlist('conditions')
    appointment = request.form['appointment']

    # منع الحجز المكرر
    if appointment in get_booked_slots(date):
        return "هذا الموعد محجوز بالفعل، يرجى اختيار وقت آخر."

    # تخزين الحجز في قاعدة البيانات
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

    # إرسال بريد إلكتروني
    message = f"""
    New Patient Booking:
    Name: {name}
    Age: {age}
    Phone: {phone}
    Date: {date}
    Pain: {pain}
    Conditions: {', '.join(conditions)}
    Appointment Time: {appointment}
    """
    send_email("tetoelsalahy@gmail.com", "New Patient Booking", message)

    return redirect('/confirmation')

@app.route('/confirmation')
def confirmation():
    return render_template('confirmation.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    form = LoginForm()
    error = None
    if form.validate_on_submit():
        if form.username.data == os.getenv("ADMIN_USERNAME") and form.password.data == os.getenv("ADMIN_PASSWORD"):
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

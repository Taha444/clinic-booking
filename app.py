import os
import sqlite3
from flask import Flask, render_template, request, redirect
from flask_wtf import FlaskForm, CSRFProtect
from wtforms import StringField, IntegerField, TelField, DateField, SelectField, SubmitField
from wtforms.validators import DataRequired
import smtplib
from email.mime.text import MIMEText
from datetime import datetime
import pytz
from dotenv import load_dotenv

# تحميل متغيرات البيئة
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY")

csrf = CSRFProtect(app)

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

# دالة استخراج المواعيد المحجوزة من قاعدة البيانات
def get_booked_slots(date):
    conn = sqlite3.connect('clinic.db')
    cursor = conn.cursor()
    cursor.execute("SELECT appointment FROM bookings WHERE date = ?", (date,))
    booked = [row[0] for row in cursor.fetchall()]
    conn.close()
    return booked

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
    booked = get_booked_slots(date)
    if appointment in booked:
        return "هذا الموعد محجوز بالفعل، يرجى اختيار وقت آخر."

    # تخزين الحجز في قاعدة البيانات
    conn = sqlite3.connect('clinic.db')
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO bookings (name, age, phone, pain, conditions, date, appointment)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (name, age, phone, pain, ', '.join(conditions), date, appointment))
    conn.commit()
    conn.close()

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

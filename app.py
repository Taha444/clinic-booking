import os
from flask import Flask, render_template, request, redirect
import smtplib
from email.mime.text import MIMEText
from datetime import datetime
import pytz
from dotenv import load_dotenv

# تحميل متغيرات البيئة من ملف .env
load_dotenv()

app = Flask(__name__)

# كل المواعيد من 3:00 PM إلى 10:00 PM بنص ساعة
all_slots = []
for hour in range(3, 11):
    all_slots.append(f"{hour}:00 PM")
    all_slots.append(f"{hour}:30 PM")

# تخزين المواعيد المحجوزة حسب التاريخ
booked_slots_by_date = {}

@app.route('/')
def index():
    egypt_time = datetime.now(pytz.timezone('Africa/Cairo'))
    today_str = egypt_time.strftime('%Y-%m-%d')
    date = request.args.get('date', today_str)

    booked = booked_slots_by_date.get(date, [])
    available_times = [slot for slot in all_slots if slot not in booked]

    return render_template('index.html', available_times=available_times, selected_date=date)

@app.route('/submit', methods=['POST'])
def submit():
    name = request.form['name']
    age = request.form['age']
    phone = request.form['phone']
    date = request.form['date']
    pain = request.form['pain']
    conditions = request.form.getlist('conditions')
    appointment = request.form['appointment']

    if date not in booked_slots_by_date:
        booked_slots_by_date[date] = []
    booked_slots_by_date[date].append(appointment)

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
    sender = "elhadyclinic1@gmail.com"
    password = os.getenv("EMAIL_PASSWORD")  # تم استبدال كلمة السر بمتغير بيئي

    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = sender
    msg['To'] = to
    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
        server.login(sender, password)
        server.sendmail(sender, to, msg.as_string())

if __name__ == '__main__':
    app.run(debug=True)

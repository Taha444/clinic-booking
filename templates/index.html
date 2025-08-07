from flask import Flask, render_template, request, redirect
import smtplib
from email.mime.text import MIMEText

app = Flask(__name__)

# Time slots from 3:00 PM to 10:00 PM
all_slots = [f"{hour}:00 PM" for hour in range(3, 10)] + ["10:00 PM"]
booked_slots = []  # هيخزن المواعيد بصيغة "YYYY-MM-DD HH:MM AM/PM"

@app.route('/')
def index():
    date = request.args.get('date', '')  # التاريخ اللي المستخدم اختاره
    available_times = [slot for slot in all_slots if f"{date} {slot}" not in booked_slots]
    return render_template('index.html', available_times=available_times)

@app.route('/submit', methods=['POST'])
def submit():
    name = request.form['name']
    age = request.form['age']
    phone = request.form['phone']
    date = request.form['date']
    pain = request.form['pain']
    conditions = request.form.getlist('conditions')
    appointment = request.form['appointment']

    # نحجز الميعاد مع التاريخ
    booked_slots.append(f"{date} {appointment}")

    # Email content
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

    # Send email
    send_email("tetoelsalahy@gmail.com", "New Patient Booking", message)

    return redirect('/confirmation')

@app.route('/confirmation')
def confirmation():
    return render_template('confirmation.html')

def send_email(to, subject, body):
    sender = "rokayanarrators@gmail.com  # حط هنا الإيميل اللي هيبعت منه
    password = "snub khwy olwk dwdj"  # والباسورد بتاعه

    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = sender
    msg['To'] = to

    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
        server.login(sender, password)
        server.sendmail(sender, to, msg.as_string())

if __name__ == '__main__':
    app.run(debug=True)

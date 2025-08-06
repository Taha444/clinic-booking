from flask import Flask, render_template, request, redirect
import smtplib
from email.mime.text import MIMEText

app = Flask(__name__)

# Time slots from 3:00 PM to 10:00 PM
all_slots = [f"{hour}:00 PM" for hour in range(3, 10)] + ["10:00 PM"]
booked_slots = []

@app.route('/')
def index():
    available_times = [slot for slot in all_slots if slot not in booked_slots]
    return render_template('index.html', available_times=available_times)

@app.route('/submit', methods=['POST'])
def submit():
    name = request.form['name']
    age = request.form['age']
    pain = request.form['pain']
    conditions = request.form.getlist('conditions')
    appointment = request.form['appointment']

    booked_slots.append(appointment)

    # Email content
    message = f"""
    New Patient Booking:
    Name: {name}
    Age: {age}
    Pain: {pain}
    Conditions: {', '.join(conditions)}
    Appointment: {appointment}
    """

    # Send email
    send_email("tetoelsalahy@gmail.com", "New Patient Booking", message)

    return redirect('/confirmation')

@app.route('/confirmation')
def confirmation():
    return render_template('confirmation.html')

def send_email(to, subject, body):
    sender = "rokayanarrators@gmail.com"
    password = "snub khwy olwk dwdj"

    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = sender
    msg['To'] = to

    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
        server.login(sender, password)
        server.sendmail(sender, to, msg.as_string())

if __name__ == '__main__':
    app.run(debug=True)

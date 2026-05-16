import os, threading, html, csv, io, secrets
from werkzeug.utils import secure_filename
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
from werkzeug.security import check_password_hash, generate_password_hash
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from sqlalchemy.exc import IntegrityError

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", secrets.token_hex(32))

app.config['SESSION_COOKIE_SECURE']   = True
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Strict'
# ✅ PostgreSQL في production، SQLite للـ development
_db_url = os.getenv('DATABASE_URL', 'sqlite:///clinic.db')
# Railway بيبعت postgres:// — SQLAlchemy محتاج postgresql://
if _db_url.startswith('postgres://'):
    _db_url = _db_url.replace('postgres://', 'postgresql://', 1)
app.config['SQLALCHEMY_DATABASE_URI'] = _db_url
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_pre_ping': True,       # تحقق إن الconnection شغال
    'pool_recycle':  300,        # أعد الconnection كل 5 دقائق
    'pool_size':     10,         # max connections في نفس الوقت
    'max_overflow':  20,         # connections إضافية وقت الضغط
}
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER']    = os.path.join(os.path.dirname(__file__), 'static')
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024   # 5MB max
ALLOWED_EXT = {'png', 'jpg', 'jpeg', 'webp'}

db     = SQLAlchemy(app)
csrf = CSRFProtect(app)

# Rate Limiter — يستخدم الـ DB لو PostgreSQL متاح، وإلا RAM
_redis_url = os.getenv('REDIS_URL')
_limiter_storage = _redis_url if _redis_url else "memory://"
limiter = Limiter(
    get_remote_address,
    app=app,
    storage_uri=_limiter_storage,
    default_limits=["500 per hour"]
)

CAIRO = pytz.timezone('Africa/Cairo')

# ─────────────────────────────────────────────
#  SECURITY HEADERS
# ─────────────────────────────────────────────
@app.before_request
def force_https():
    """Redirect HTTP to HTTPS in production"""
    if not request.is_secure and request.headers.get('X-Forwarded-Proto', 'http') == 'http':
        if not app.debug and 'railway' in request.headers.get('Host', '').lower():
            url = request.url.replace('http://', 'https://', 1)
            return redirect(url, code=301)


@app.after_request
def security_headers(response):
    response.headers['X-Frame-Options']           = 'DENY'
    response.headers['X-Content-Type-Options']    = 'nosniff'
    response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains; preload'
    # ✅ Content Security Policy
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "script-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "connect-src 'self';"
    )
    response.headers['Referrer-Policy']           = 'strict-origin-when-cross-origin'
    response.headers['Permissions-Policy']        = 'geolocation=(), microphone=(), camera=()'
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
    status      = db.Column(db.String(20),  default='confirmed')   # confirmed/cancelled/attended
    cancel_token= db.Column(db.String(64),  unique=True)           # توكن الإلغاء
    created_at  = db.Column(db.DateTime,    default=datetime.utcnow)
    reminder_sent = db.Column(db.Boolean,   default=False)

    __table_args__ = {}  # UniqueConstraint شيلناه — الـ check بيتم في الكود
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


class ClinicSettings(db.Model):
    """إعدادات العيادة — صف واحد دائماً"""
    id            = db.Column(db.Integer, primary_key=True)
    # أوقات العمل
    start_hour    = db.Column(db.Integer, default=15)   # 3 PM = 15 في 24h format
    start_minute  = db.Column(db.String(2), default='00')
    end_hour      = db.Column(db.Integer, default=23)   # 11 PM = 23 في 24h format
    slot_duration = db.Column(db.Integer, default=30)   # بالدقائق
    # أيام العمل (0=الأحد ... 6=السبت) — مخزنة كـ JSON string
    work_days     = db.Column(db.String(20), default='0,1,2,3,5,6')  # بدون الجمعة
    # إجازات استثنائية (تواريخ مفصولة بفاصلة)
    holidays      = db.Column(db.Text, default='')
    updated_at    = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AdminCredentials(db.Model):
    """بيانات الأدمن — مخزّنة في DB عشان تتغير أوتوماتيك"""
    id            = db.Column(db.Integer, primary_key=True)
    username      = db.Column(db.String(100), nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    updated_at    = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


with app.app_context():
    db.create_all()
    # تصحيح الإعدادات القديمة لو start_hour < 8 (يعني مخزّن بالطريقة القديمة)
    old_settings = ClinicSettings.query.first()
    if old_settings and old_settings.start_hour < 8:
        old_settings.start_hour = old_settings.start_hour + 12  # 3 → 15
        if old_settings.end_hour < 12:
            old_settings.end_hour = old_settings.end_hour + 12  # 11 → 23
        db.session.commit()

    # seed الأدمن من الـ env variables لو DB فاضي
    if not AdminCredentials.query.first():
        env_user = os.getenv("ADMIN_USERNAME", "admin")
        env_hash = os.getenv("ADMIN_PASSWORD", "")
        if env_hash:
            db.session.add(AdminCredentials(username=env_user, password_hash=env_hash))
            db.session.commit()


# ─────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────
def get_admin():
    """جيب بيانات الأدمن من DB — fallback للـ env لو DB فاضي"""
    admin = AdminCredentials.query.first()
    if admin:
        return admin.username, admin.password_hash
    return os.getenv("ADMIN_USERNAME", "admin"), os.getenv("ADMIN_PASSWORD", "")


def get_settings():
    """جيب إعدادات العيادة — أنشئها لو مش موجودة"""
    s = ClinicSettings.query.first()
    if not s:
        s = ClinicSettings()
        db.session.add(s)
        db.session.commit()
    return s


def hour24_to_12(hour24):
    """تحويل 24h لـ 12h format — مثال: 15 → '3:00 PM'"""
    ampm = 'AM' if hour24 < 12 else 'PM'
    h12  = hour24 % 12
    if h12 == 0:
        h12 = 12
    return h12, ampm


def get_all_slots():
    """المواعيد المتاحة حسب الإعدادات — يستخدم 24h داخلياً"""
    s      = get_settings()
    slots  = []
    # start_hour مخزّن كـ 24h (مثلاً 15 = 3 PM)
    hour   = s.start_hour
    minute = int(s.start_minute)
    end_h  = s.end_hour

    while True:
        h12, ampm = hour24_to_12(hour)
        slots.append(f"{h12}:{minute:02d} {ampm}")
        minute += s.slot_duration
        if minute >= 60:
            hour  += minute // 60
            minute = minute % 60
        if hour > end_h or (hour == end_h and minute > 0):
            break
    return slots


def get_work_days():
    """أيام العمل كـ set من الأرقام (0=الأحد)"""
    s = get_settings()
    return {int(d) for d in s.work_days.split(',') if d.strip()}


def get_holidays():
    """الإجازات الاستثنائية كـ set من التواريخ"""
    s = get_settings()
    return {d.strip() for d in s.holidays.split(',') if d.strip()}


def egypt_today():
    return datetime.now(CAIRO).date()


def valid_date(date_str):
    try:
        d = datetime.strptime(date_str, '%Y-%m-%d').date()
        if d < egypt_today():
            return False, "لا يمكن الحجز في تاريخ سابق"
        # تحقق من أيام العمل (Python weekday: 0=Mon, 6=Sun)
        # نحوّل لـ format الإعدادات (0=الأحد)
        py_wd = d.weekday()  # 0=Mon..6=Sun
        # Sun=6 in python → 0 in our system, Mon=0→1, ..., Sat=5→6
        our_wd = (py_wd + 1) % 7
        if our_wd not in get_work_days():
            return False, "العيادة مغلقة في هذا اليوم"
        if date_str in get_holidays():
            return False, "هذا اليوم إجازة استثنائية"
        return True, d
    except ValueError:
        return False, "تاريخ غير صالح"


def booked_slots(date):
    return [b.appointment for b in
            Booking.query.filter(
                Booking.date == date,
                Booking.status.in_(['confirmed', 'attended'])
            ).all()]


def slot_to_minutes(slot_str):
    """حوّل slot string لـ minutes من منتصف الليل — مثال: '3:00 PM' → 900"""
    try:
        time_part, ampm = slot_str.strip().rsplit(' ', 1)
        h, m = map(int, time_part.split(':'))
        if ampm.upper() == 'PM' and h != 12:
            h += 12
        elif ampm.upper() == 'AM' and h == 12:
            h = 0
        return h * 60 + m
    except:
        return 9999


def filter_past_slots(slots, date_str):
    """شيل المواعيد اللي فاتت لو التاريخ هو اليوم"""
    today = egypt_today()
    try:
        selected = datetime.strptime(date_str, '%Y-%m-%d').date()
    except:
        return slots

    if selected != today:
        return slots  # لو مش اليوم — كل المواعيد متاحة

    # احسب الوقت الحالي بتوقيت القاهرة بالدقائق
    now_cairo   = datetime.now(CAIRO)
    now_minutes = now_cairo.hour * 60 + now_cairo.minute + 30  # buffer 30 دقيقة

    return [s for s in slots if slot_to_minutes(s) > now_minutes]


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
#  SMS  (Twilio — للمريض)
# ─────────────────────────────────────────────
def send_sms(to_phone, message):
    """إرسال SMS عبر Twilio للمريض"""
    try:
        from twilio.rest import Client
        sid   = os.getenv("TWILIO_ACCOUNT_SID")
        token = os.getenv("TWILIO_AUTH_TOKEN")
        from_ = os.getenv("TWILIO_FROM_NUMBER")
        if not all([sid, token, from_]):
            app.logger.info("Twilio not configured, skipping SMS")
            return
        # الرقم المصري: نضيف +2 في الأول لو مش موجود
        if not to_phone.startswith('+'):
            to_phone = '+2' + to_phone
        client = Client(sid, token)
        client.messages.create(body=message, from_=from_, to=to_phone)
        app.logger.info(f"SMS sent to {to_phone}")
    except Exception as e:
        app.logger.error(f"SMS error: {e}")


# ─────────────────────────────────────────────
#  WHATSAPP  (CallMeBot — للدكتور)
# ─────────────────────────────────────────────
def send_whatsapp(phone, message):
    """إرسال واتساب للدكتور عبر CallMeBot"""
    try:
        import urllib.request, urllib.parse
        api_key = os.getenv("CALLMEBOT_API_KEY")
        if not api_key:
            return
        url = (f"https://api.callmebot.com/whatsapp.php"
               f"?phone={phone}&text={urllib.parse.quote(message)}&apikey={api_key}")
        urllib.request.urlopen(url, timeout=10)
    except Exception as e:
        app.logger.error(f"WhatsApp error: {e}")


def get_doctor_phone():
    return os.getenv("DOCTOR_PHONE", "")


def notify_booking(name, phone, date, appointment):
    """إشعار عند حجز جديد: SMS للمريض + واتساب للدكتور"""
    # ── SMS للمريض ──
    patient_msg = (
        f"مركز الهادي للعلاج الطبيعي\n"
        f"تم تاكيد حجزك بنجاح\n"
        f"الاسم: {name}\n"
        f"التاريخ: {date}\n"
        f"الميعاد: {appointment}\n"
        f"يرجى الحضور قبل الموعد بـ 10 دقائق"
    )
    threading.Thread(target=send_sms, args=(phone, patient_msg), daemon=True).start()

    # ── واتساب للدكتور ──
    doctor = get_doctor_phone()
    if doctor:
        doctor_msg = (
            f"حجز جديد - مركز الهادي\n"
            f"الاسم: {name}\n"
            f"الهاتف: {phone}\n"
            f"التاريخ: {date}\n"
            f"الميعاد: {appointment}"
        )
        threading.Thread(target=send_whatsapp, args=(doctor, doctor_msg), daemon=True).start()


def notify_reminder(name, phone, date, appointment):
    """تذكير قبل الموعد بـ 24 ساعة: SMS للمريض + واتساب للدكتور"""
    # ── SMS للمريض ──
    patient_msg = (
        f"تذكير - مركز الهادي للعلاج الطبيعي\n"
        f"لديك موعد غداً\n"
        f"التاريخ: {date}\n"
        f"الميعاد: {appointment}\n"
        f"يرجى الحضور قبل الموعد بـ 10 دقائق"
    )
    threading.Thread(target=send_sms, args=(phone, patient_msg), daemon=True).start()

    # ── واتساب للدكتور ──
    doctor = get_doctor_phone()
    if doctor:
        doctor_msg = (
            f"تذكير موعد غد - مركز الهادي\n"
            f"الاسم: {name}\n"
            f"الهاتف: {phone}\n"
            f"التاريخ: {date}\n"
            f"الميعاد: {appointment}"
        )
        threading.Thread(target=send_whatsapp, args=(doctor, doctor_msg), daemon=True).start()


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

    free  = filter_past_slots([s for s in get_all_slots() if s not in booked_slots(date)], date)
    form  = BookingForm()
    form.appointment.choices = [(t, t) for t in free] if free else [('', 'لا توجد مواعيد')]
    form.date.data = datetime.strptime(date, '%Y-%m-%d')
    return render_template('index.html', form=form, available_times=free, selected_date=date)


@app.route('/available_slots')
@limiter.limit("30 per minute")
def available_slots():
    date = request.args.get('date')
    if not date:
        return jsonify({'available_times': [], 'error': 'التاريخ مطلوب'})
    ok, result = valid_date(date)
    if not ok:
        return jsonify({'available_times': [], 'error': result})
    return jsonify({'available_times': filter_past_slots([s for s in get_all_slots() if s not in booked_slots(date)], date)})


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

    # ✅ فحص أولي — لو الرقم مسجل مسبقاً وجّهه لصفحة المرضى القدامى
    if phone and phone.isdigit() and len(phone) >= 10:
        existing = PatientProfile.query.filter_by(phone=phone).first()
        if existing:
            return redirect(f'/returning?phone={phone}&redirect=blocked')

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
    elif date_str and not filter_past_slots([appointment], date_str):
                                           errors.append("هذا الميعاد قد مضى، يرجى اختيار ميعاد آخر")
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
#  RETURNING PATIENT — صفحة المريض القديم
# ─────────────────────────────────────────────
@app.route('/returning', methods=['GET', 'POST'])
@limiter.limit("5 per minute")
def returning_patient():
    """صفحة خاصة بالمرضى القدامى — يدخل رقمه ويحجز مباشرة"""
    phone        = request.args.get('phone', '').strip()
    redirect_msg = request.args.get('redirect', '')
    patient      = None
    not_found    = False

    # لو جه من فورم البحث
    if request.method == 'POST' and 'lookup_phone' in request.form:
        phone   = request.form.get('lookup_phone', '').strip()
        patient = PatientProfile.query.filter_by(phone=phone).first()
        if not patient:
            not_found = True

    # لو جه من redirect بعد رفض submit
    if phone and not patient:
        patient = PatientProfile.query.filter_by(phone=phone).first()

    # معالجة حجز المريض القديم
    if request.method == 'POST' and 'appointment' in request.form:
        phone       = request.form.get('phone', '').strip()
        pain        = request.form.get('pain', '').strip()
        date_str    = request.form.get('date', '').strip()
        appointment = request.form.get('appointment', '').strip()
        conditions  = request.form.getlist('conditions')
        patient     = PatientProfile.query.filter_by(phone=phone).first()

        if not patient:
            flash("رقم الهاتف غير مسجل لدينا", 'error')
            return redirect('/returning')

        errors = []
        if not pain:                        errors.append("يرجى وصف الشكوى")
        if not date_str:                    errors.append("التاريخ مطلوب")
        else:
            ok, res = valid_date(date_str)
            if not ok:                      errors.append(res)
        if not appointment:                 errors.append("يرجى اختيار ميعاد")
        elif date_str and appointment in booked_slots(date_str):
                                            errors.append("هذا الموعد محجوز بالفعل")
        elif date_str and not filter_past_slots([appointment], date_str):
                                            errors.append("هذا الميعاد قد مضى، يرجى اختيار ميعاد آخر")
        if errors:
            for e in errors: flash(e, 'error')
            return redirect(f'/returning?phone={phone}')

        token = secrets.token_urlsafe(32)
        try:
            b = Booking(
                name        = patient.name,
                age         = patient.age or 0,
                phone       = patient.phone,
                pain        = html.escape(pain),
                conditions  = ', '.join(conditions) or patient.conditions or '',
                date        = date_str,
                appointment = appointment,
                cancel_token= token)
            db.session.add(b)
            db.session.flush()
            upsert_patient(b)
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("هذا الموعد محجوز بالفعل، يرجى اختيار وقت آخر.", 'error')
            return redirect(f'/returning?phone={phone}')
        except Exception as e:
            db.session.rollback()
            app.logger.error(f"DB error: {e}")
            flash("حدث خطأ، يرجى المحاولة مرة أخرى.", 'error')
            return redirect(f'/returning?phone={phone}')

        notify_booking(patient.name, patient.phone, date_str, appointment)
        return redirect(f'/confirmation?token={token}')

    # GET — اعرض الصفحة
    today       = egypt_today().strftime('%Y-%m-%d')
    free        = filter_past_slots([s for s in get_all_slots() if s not in booked_slots(today)], today)
    form        = BookingForm()
    form.appointment.choices = [(t, t) for t in free] if free else [('', 'لا توجد مواعيد')]

    return render_template('returning_patient.html',
        patient=patient, phone=phone,
        redirect_msg=redirect_msg, not_found=not_found,
        available_times=free, selected_date=today,
        form=form, today=today)


# ─────────────────────────────────────────────
#  CANCEL / EDIT BOOKING  (بدون login)
# ─────────────────────────────────────────────
@app.route('/cancel/<token>')
def cancel_booking_page(token):
    b = Booking.query.filter_by(cancel_token=token).first_or_404()
    return render_template('cancel_booking.html', booking=b)


@app.route('/cancel/<token>/confirm', methods=['POST'])
@limiter.limit("5 per minute")
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
        admin_user, admin_hash = get_admin()
        if (form.username.data == admin_user and
                check_password_hash(admin_hash, form.password.data)):
            session['admin_logged_in'] = True
            return redirect('/dashboard')
        error = "بيانات الدخول غير صحيحة"
    return render_template('login.html', form=form, error=error)


@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')


@app.route('/change_password', methods=['GET', 'POST'])
@admin_required
def change_password():
    error = None

    if request.method == 'POST':
        current      = request.form.get('current_password', '')
        new_pass     = request.form.get('new_password', '').strip()
        confirm      = request.form.get('confirm_password', '').strip()
        new_username = request.form.get('new_username', '').strip()

        _, stored_hash = get_admin()
        admin_rec      = AdminCredentials.query.first()

        if not check_password_hash(stored_hash, current):
            error = "كلمة السر الحالية غير صحيحة"
        elif len(new_pass) < 8:
            error = "كلمة السر الجديدة يجب أن تكون 8 أحرف على الأقل"
        elif new_pass != confirm:
            error = "كلمة السر الجديدة وتأكيدها غير متطابقين"
        elif current == new_pass:
            error = "كلمة السر الجديدة يجب أن تختلف عن الحالية"
        else:
            new_hash = generate_password_hash(new_pass)
            if admin_rec:
                admin_rec.password_hash = new_hash
                if new_username:
                    admin_rec.username = new_username
            else:
                # أنشئ record جديد
                admin_user, _ = get_admin()
                db.session.add(AdminCredentials(
                    username=new_username or admin_user,
                    password_hash=new_hash))
            db.session.commit()
            # خروج تلقائي — يسجّل دخول بكلمة السر الجديدة
            session.clear()
            flash("✅ تم تغيير كلمة السر بنجاح! سجّل دخولك من جديد.", 'success')
            return redirect('/login')

    current_username, _ = get_admin()
    return render_template('change_password.html',
                           error=error,
                           current_username=current_username)


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
    attended   = Booking.query.filter_by(status='attended').count()

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
        patients_n=patients_n, cancelled=cancelled, attended=attended,
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
    date_from   = request.args.get('date_from', '').strip()
    date_to     = request.args.get('date_to', '').strip()
    status_f    = request.args.get('status_f', '').strip()     # confirmed/cancelled/attended
    condition_f = request.args.get('condition_f', '').strip()  # Diabetes/High Blood Pressure/Old Injury
    time_f      = request.args.get('time_f', '').strip()       # AM/PM slot
    sort_by     = request.args.get('sort_by', 'date_asc')      # date_asc/date_desc/name
    view        = request.args.get('view', 'table')

    query = Booking.query

    if search:
        query = query.filter(db.or_(
            Booking.name.ilike(f'%{search}%'),
            Booking.phone.ilike(f'%{search}%')))
    if date_from:
        query = query.filter(Booking.date >= date_from)
    if date_to:
        query = query.filter(Booking.date <= date_to)
    if status_f:
        query = query.filter(Booking.status == status_f)
    if condition_f:
        query = query.filter(Booking.conditions.ilike(f'%{condition_f}%'))
    if time_f == 'AM':
        query = query.filter(Booking.appointment.ilike('%AM%'))
    elif time_f == 'PM':
        query = query.filter(Booking.appointment.ilike('%PM%'))

    if sort_by == 'date_desc':
        query = query.order_by(Booking.date.desc(), Booking.appointment)
    elif sort_by == 'name':
        query = query.order_by(Booking.name)
    else:
        query = query.order_by(Booking.date, Booking.appointment)

    all_bookings = query.all()

    # إحصائيات سريعة للفلتر الحالي
    stats = {
        'confirmed': sum(1 for b in all_bookings if b.status == 'confirmed'),
        'attended':  sum(1 for b in all_bookings if b.status == 'attended'),
        'cancelled': sum(1 for b in all_bookings if b.status == 'cancelled'),
    }

    # بيانات Calendar
    cal_data = {}
    if view == 'calendar':
        today = egypt_today()
        first = today.replace(day=1)
        last  = (first.replace(month=first.month % 12 + 1, day=1)
                 if first.month < 12 else first.replace(year=first.year+1, month=1, day=1))
        month_bookings = Booking.query.filter(
            Booking.date >= first.strftime('%Y-%m-%d'),
            Booking.date <  last.strftime('%Y-%m-%d'),
            Booking.status.in_(['confirmed', 'attended'])).all()
        for b in month_bookings:
            cal_data.setdefault(b.date, []).append(b)

    # الفلاتر النشطة
    active_filters = any([search, date_from, date_to, status_f, condition_f, time_f])

    return render_template('bookings.html',
        bookings=all_bookings, search=search,
        date_from=date_from, date_to=date_to,
        status_f=status_f, condition_f=condition_f,
        time_f=time_f, sort_by=sort_by,
        view=view, cal_data=cal_data,
        today=egypt_today().strftime('%Y-%m-%d'),
        stats=stats, active_filters=active_filters)


@app.route('/delete_booking/<int:bid>', methods=['POST'])
@admin_required
@limiter.limit("20 per minute")
def delete_booking(bid):
    b = Booking.query.get_or_404(bid)
    # احذف التقييم والملاحظات المرتبطة أولاً قبل الحجز
    BookingRating.query.filter_by(booking_id=bid).delete()
    SessionNote.query.filter_by(booking_id=bid).delete()
    db.session.delete(b)
    db.session.commit()
    flash("تم حذف الحجز", 'success')
    return redirect('/bookings')


@app.route('/attend_booking/<int:bid>', methods=['POST'])
@admin_required
@limiter.limit("30 per minute")
def attend_booking(bid):
    b = Booking.query.get_or_404(bid)
    if b.status == 'confirmed':
        b.status = 'attended'
        flash(f"✅ تم تأكيد حضور {b.name}", 'success')
    elif b.status == 'attended':
        b.status = 'confirmed'
        flash("↩️ تم إلغاء تأكيد الحضور", 'success')
    db.session.commit()
    return redirect(request.referrer or '/bookings')


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
#  ADMIN — RATINGS
# ─────────────────────────────────────────────
@app.route('/ratings')
@admin_required
def ratings():
    all_ratings = (BookingRating.query
                   .order_by(BookingRating.created_at.desc())
                   .all())
    avg = round(sum(r.stars for r in all_ratings) / len(all_ratings), 1) if all_ratings else 0
    dist = {i: sum(1 for r in all_ratings if r.stars == i) for i in range(1, 6)}
    return render_template('ratings.html',
                           ratings=all_ratings, avg=avg,
                           dist=dist, total=len(all_ratings))



# ─────────────────────────────────────────────
#  ADMIN — CLINIC SETTINGS
# ─────────────────────────────────────────────
@app.route('/settings', methods=['GET', 'POST'])
@admin_required
def clinic_settings():
    s = get_settings()

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'hours':
            s.start_hour    = int(request.form.get('start_hour', 15))  # 24h
            s.start_minute  = request.form.get('start_minute', '00')
            s.end_hour      = int(request.form.get('end_hour', 23))    # 24h
            s.slot_duration = int(request.form.get('slot_duration', 30))
            db.session.commit()
            flash('✅ تم حفظ أوقات العمل', 'success')

        elif action == 'days':
            days = request.form.getlist('work_days')
            s.work_days = ','.join(days) if days else ''
            db.session.commit()
            flash('✅ تم حفظ أيام العمل', 'success')

        elif action == 'add_holiday':
            date = request.form.get('holiday_date', '').strip()
            if date:
                existing = {d.strip() for d in s.holidays.split(',') if d.strip()}
                existing.add(date)
                s.holidays = ','.join(sorted(existing))
                db.session.commit()
                flash(f'✅ تمت إضافة إجازة {date}', 'success')

        elif action == 'remove_holiday':
            date = request.form.get('holiday_date', '').strip()
            if date:
                existing = {d.strip() for d in s.holidays.split(',') if d.strip()}
                existing.discard(date)
                s.holidays = ','.join(sorted(existing))
                db.session.commit()
                flash(f'تم حذف إجازة {date}', 'success')

        return redirect('/settings')

    # حسب المواعيد الحالية
    slots_preview = get_all_slots()
    work_days_set = get_work_days()
    holidays_list = sorted([d for d in s.holidays.split(',') if d.strip()])

    return render_template('clinic_settings.html',
        s=s, slots_preview=slots_preview,
        work_days_set=work_days_set,
        holidays_list=holidays_list,
        today=egypt_today().strftime('%Y-%m-%d'))

# ─────────────────────────────────────────────
#  ADMIN — DOCTOR PHOTO UPLOAD
# ─────────────────────────────────────────────
@app.route('/upload_photo', methods=['GET', 'POST'])
@admin_required
def upload_photo():
    if request.method == 'POST':
        f = request.files.get('photo')
        if not f or f.filename == '':
            flash('يرجى اختيار صورة', 'error')
            return redirect('/upload_photo')
        ext = f.filename.rsplit('.', 1)[-1].lower()
        if ext not in ALLOWED_EXT:
            flash('صيغة غير مدعومة — يُسمح فقط بـ JPG, PNG, WEBP', 'error')
            return redirect('/upload_photo')
        # احفظها باسم ثابت doctor-clean.<ext>
        filename = f'doctor-clean.{ext}'
        save_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        # احذف الصورة القديمة لو بصيغة مختلفة
        for old_ext in ALLOWED_EXT:
            old_path = os.path.join(app.config['UPLOAD_FOLDER'], f'doctor-clean.{old_ext}')
            if os.path.exists(old_path) and old_ext != ext:
                os.remove(old_path)
        f.save(save_path)
        flash('✅ تم رفع الصورة بنجاح', 'success')
        return redirect('/upload_photo')
    # GET — اعرض الصفحة
    current = None
    for ext in ALLOWED_EXT:
        p = os.path.join(app.config['UPLOAD_FOLDER'], f'doctor-clean.{ext}')
        if os.path.exists(p):
            current = f'doctor-clean.{ext}'
            break
    return render_template('upload_photo.html', current=current)

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

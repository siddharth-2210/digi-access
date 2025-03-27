from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime

db = SQLAlchemy()

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(128))
    name = db.Column(db.String(100))
    veg_meals_count = db.Column(db.Integer, default=0)
    non_veg_meals_count = db.Column(db.Integer, default=0)
    balance_money = db.Column(db.Float, default=0.0)
    veg_meals_spent = db.Column(db.Float, default=0.0)
    non_veg_meals_spent = db.Column(db.Float, default=0.0)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def total_money_spent(self):
        return self.veg_meals_spent + self.non_veg_meals_spent

class MealMenu(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False)
    meal_type = db.Column(db.String(20), nullable=False)  # Breakfast, Lunch, Dinner
    veg_menu = db.Column(db.Text, nullable=False)
    non_veg_menu = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<MealMenu {self.meal_type} on {self.date}>'

class MealAttendance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    menu_id = db.Column(db.Integer, db.ForeignKey('meal_menu.id'), nullable=False)
    meal_choice = db.Column(db.String(10), nullable=False)  # 'veg' or 'non_veg'
    attended_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships
    user = db.relationship('User', backref=db.backref('meal_attendances', lazy=True))
    menu = db.relationship('MealMenu', backref=db.backref('attendances', lazy=True)) 
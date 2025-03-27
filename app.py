from flask import Flask, render_template, redirect, url_for, flash, request, jsonify
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from datetime import datetime, date, timedelta
from models import db, User, MealMenu, MealAttendance
from sqlalchemy import func, case
import os
import qrcode
import base64
import io
import secrets
from functools import wraps

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-here'  # Change this to a secure secret key
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///mess_system.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

# Store temporary verification tokens
verification_tokens = {}

# Meal prices
VEG_MEAL_PRICE = 50.0
NON_VEG_MEAL_PRICE = 80.0

# Initialize Flask-Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# Initialize SQLAlchemy
db.init_app(app)

def generate_qr_code(data):
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(data)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")
    img_buffer = io.BytesIO()
    img.save(img_buffer, format='PNG')
    img_str = base64.b64encode(img_buffer.getvalue()).decode()
    return img_str

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def is_meal_time():
    current_time = datetime.now()
    breakfast = (7, 0) <= (current_time.hour, current_time.minute) <= (9, 0)
    lunch = (12, 0) <= (current_time.hour, current_time.minute) <= (14, 0)
    dinner = (2, 0) <= (current_time.hour, current_time.minute) <= (3, 0)
    
    if breakfast:
        return "Breakfast"
    elif lunch:
        return "Lunch"
    elif dinner:
        return "Dinner"
    return None

def get_current_meal_menu():
    current_meal = is_meal_time()
    if current_meal:
        return MealMenu.query.filter_by(
            date=date.today(),
            meal_type=current_meal
        ).first()
    return None

@app.route('/')
def home():
    # Log out the user when they return to the homepage
    if current_user.is_authenticated:
        logout_user()
        # Don't flash a message to avoid confusing the user
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    # Always check if already logged in
    if current_user.is_authenticated:
        # If logged in, log out first to start fresh
        logout_user()
    
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        user = User.query.filter_by(email=email).first()
        
        if user and user.check_password(password):
            login_user(user)
            next_page = request.args.get('next')
            if next_page:
                return redirect(next_page)
            return redirect(url_for('check_meal_time'))
        else:
            flash('Invalid email or password')
    
    return render_template('login.html')

@app.route('/check-meal-time')
@login_required
def check_meal_time():
    current_meal = is_meal_time()
    current_menu = get_current_meal_menu()
    
    if current_meal and current_menu:
        # Check if student has already consumed this meal
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = today_start + timedelta(days=1)
        
        # Handle dinner time crossing midnight
        if current_meal == "Dinner":
            # If it's between midnight and 3 AM, check previous day's dinner
            if datetime.now().hour < 4:
                today_start = today_start - timedelta(days=1)
            # If it's before midnight, check current day's dinner
            else:
                today_start = today_start.replace(hour=19, minute=0)
                today_end = today_end.replace(hour=3, minute=0) + timedelta(days=1)
        elif current_meal == "Breakfast":
            today_start = today_start.replace(hour=7, minute=0)
            today_end = today_start.replace(hour=9, minute=0)
        else:  # Lunch
            today_start = today_start.replace(hour=12, minute=0)
            today_end = today_start.replace(hour=14, minute=0)
        
        # Check for existing attendance in this meal period
        existing_attendance = MealAttendance.query.filter(
            MealAttendance.user_id == current_user.id,
            MealAttendance.menu_id == current_menu.id,
            MealAttendance.attended_at.between(today_start, today_end)
        ).first()
        
        if existing_attendance:
            return render_template('meal_already_consumed.html',
                                meal_type=current_meal,
                                consumed_at=existing_attendance.attended_at,
                                meal_choice=existing_attendance.meal_choice)
        
        return render_template('meal_selection.html', 
                             meal_time=current_meal, 
                             menu=current_menu)
    else:
        return render_template('no_meal.html')

@app.route('/submit-meal-choice', methods=['POST'])
@login_required
def submit_meal_choice():
    meal_choice = request.form.get('meal_choice')
    current_menu = get_current_meal_menu()
    
    if not current_menu:
        flash('No meal is currently being served.')
        return redirect(url_for('check_meal_time'))
    
    # Generate verification token
    token = secrets.token_urlsafe(32)
    verification_data = {
        'user_id': current_user.id,
        'menu_id': current_menu.id,
        'meal_choice': meal_choice,
        'timestamp': datetime.now(),
        'used': False
    }
    verification_tokens[token] = verification_data
    
    # Generate QR code
    qr_data = f"{request.host_url}verify-meal/{token}"
    qr_code = generate_qr_code(qr_data)
    
    return render_template('qr_verification.html',
                         qr_code=qr_code,
                         meal_choice=meal_choice,
                         meal_time=current_menu.meal_type,
                         current_time=datetime.now(),
                         verification_token=token)

@app.route('/scan-qr')
@login_required
def scan_qr():
    current_meal = is_meal_time()
    current_menu = get_current_meal_menu()
    return render_template('scan_qr.html',
                         current_meal=current_meal,
                         menu=current_menu,
                         current_time=datetime.now())

@app.route('/simulate-verification', methods=['POST'])
@login_required
def simulate_verification():
    current_menu = get_current_meal_menu()
    if not current_menu:
        return jsonify({
            'success': False,
            'error': 'No meal is currently being served'
        })
    
    try:
        # Generate a verification token
        token = secrets.token_urlsafe(32)
        verification_data = {
            'user_id': current_user.id,
            'menu_id': current_menu.id,
            'meal_choice': 'veg',  # Default to veg for simulation
            'timestamp': datetime.now(),
            'used': False
        }
        verification_tokens[token] = verification_data
        
        return jsonify({
            'success': True,
            'redirect_url': url_for('verify_meal', token=token)
        })
    
    except Exception as e:
        return jsonify({
            'success': False,
            'error': 'Failed to process verification'
        })

@app.route('/verify-meal/<token>')
@login_required
def verify_meal(token):
    if token not in verification_tokens:
        return render_template('transaction_status.html',
                             success=False,
                             error_message='Invalid or expired QR code')
    
    verification_data = verification_tokens[token]
    if verification_data['used']:
        return render_template('transaction_status.html',
                             success=False,
                             error_message='This QR code has already been used')
    
    # Mark token as used
    verification_data['used'] = True
    
    try:
        # Get user and menu information
        user = User.query.get(verification_data['user_id'])
        menu = MealMenu.query.get(verification_data['menu_id'])
        meal_choice = verification_data['meal_choice']
        price = VEG_MEAL_PRICE if meal_choice == 'veg' else NON_VEG_MEAL_PRICE
        
        if not user or not menu:
            return render_template('transaction_status.html',
                                 success=False,
                                 error_message='Invalid user or menu data')
        
        if user.balance_money < price:
            return render_template('transaction_status.html',
                                 success=False,
                                 error_message='Insufficient balance')
        
        # Start database transaction
        try:
            # Create new meal attendance record
            attendance = MealAttendance(
                user_id=user.id,
                menu_id=menu.id,
                meal_choice=meal_choice,
                attended_at=datetime.now()
            )
            db.session.add(attendance)
            
            # Update user's balance and spending
            previous_balance = user.balance_money
            user.balance_money -= price
            
            if meal_choice == 'veg':
                user.veg_meals_count += 1
                user.veg_meals_spent += price
            else:
                user.non_veg_meals_count += 1
                user.non_veg_meals_spent += price
            
            # Generate transaction ID
            db.session.flush()
            transaction_id = f"TXN-{datetime.now().strftime('%Y%m%d')}-{attendance.id:04d}"
            
            db.session.commit()
            
            return render_template('transaction_status.html',
                                 success=True,
                                 student_name=user.name,
                                 meal_type=f"{menu.meal_type} ({meal_choice.title()})",
                                 transaction_time=attendance.attended_at,
                                 amount=price,
                                 balance=user.balance_money,
                                 previous_balance=previous_balance,
                                 veg_count=user.veg_meals_count,
                                 non_veg_count=user.non_veg_meals_count,
                                 veg_spent=user.veg_meals_spent,
                                 non_veg_spent=user.non_veg_meals_spent,
                                 transaction_id=transaction_id)
        
        except Exception as e:
            db.session.rollback()
            print(f"Database error: {str(e)}")
            return render_template('transaction_status.html',
                                 success=False,
                                 error_message='Database update failed. Please try again.')
    
    except Exception as e:
        print(f"General error: {str(e)}")
        return render_template('transaction_status.html',
                             success=False,
                             error_message='Transaction failed. Please try again.')

# Add this decorator to protect routes that need authentication
def staff_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/student-database')
def student_database():
    students = User.query.all()
    return render_template('student_database.html', students=students)

@app.route('/meal-attendance')
def meal_attendance():
    try:
        # Get and validate the date parameter
        selected_date_str = request.args.get('date')
        try:
            selected_date = datetime.strptime(selected_date_str, '%Y-%m-%d').date() if selected_date_str else date.today()
        except ValueError:
            selected_date = date.today()

        # Get meals for the selected date
        meals = MealMenu.query.filter_by(date=selected_date).all()
        
        # Sort meals manually in the correct order
        meal_order = {'Breakfast': 1, 'Lunch': 2, 'Dinner': 3}
        meals = sorted(meals, key=lambda x: meal_order.get(x.meal_type, 4))
        
        # Initialize attendance counts dictionary
        attendance_counts = {}
        
        # Calculate attendance counts for each meal
        for meal in meals:
            # Get all attendances for this meal
            attendances = MealAttendance.query.filter_by(menu_id=meal.id).all()
            
            # Count veg and non-veg meals
            veg_count = sum(1 for a in attendances if a.meal_choice == 'veg')
            non_veg_count = sum(1 for a in attendances if a.meal_choice == 'non_veg')
            
            # Store the counts
            attendance_counts[meal.id] = {
                'total': len(attendances),
                'veg': veg_count,
                'non_veg': non_veg_count
            }
            
        # If no meals exist for this date, create them based on the weekday
        if not meals:
            weekday = selected_date.weekday()  # 0 = Monday, 6 = Sunday
            create_meals_for_date(selected_date, weekday)
            
            # Try fetching the meals again
            meals = MealMenu.query.filter_by(date=selected_date).all()
            meals = sorted(meals, key=lambda x: meal_order.get(x.meal_type, 4))
            
            # Initialize attendance counts for new meals
            for meal in meals:
                attendance_counts[meal.id] = {
                    'total': 0,
                    'veg': 0,
                    'non_veg': 0
                }

        # Calculate next and previous dates
        prev_date = (selected_date - timedelta(days=1)).strftime('%Y-%m-%d')
        next_date = (selected_date + timedelta(days=1)).strftime('%Y-%m-%d')

        return render_template('meal_attendance.html',
                            meals=meals,
                            attendance_counts=attendance_counts,
                            selected_date=selected_date,
                            prev_date=prev_date,
                            next_date=next_date,
                            timedelta=timedelta)  # Pass timedelta to template
                            
    except Exception as e:
        print(f"Error in meal_attendance route: {str(e)}")
        flash('Error loading meal attendance data. Please try again.')
        return redirect(url_for('home'))

# Helper function to create meals for a specific date based on weekday
def create_meals_for_date(target_date, weekday):
    # Define weekly meal schedule
    weekly_menus = {
        0: {  # Monday
            'Breakfast': {
                'veg': 'Idli, Sambar, Coconut Chutney',
                'non_veg': 'Egg Bhurji with Toast'
            },
            'Lunch': {
                'veg': 'Rajma Chawal, Jeera Aloo, Roti',
                'non_veg': 'Butter Chicken, Jeera Rice'
            },
            'Dinner': {
                'veg': 'Paneer Butter Masala, Pulao, Naan',
                'non_veg': 'Chicken Biryani, Raita'
            }
        },
        1: {  # Tuesday
            'Breakfast': {
                'veg': 'Poha, Jalebi, Tea',
                'non_veg': 'Omelette, Toast, Coffee'
            },
            'Lunch': {
                'veg': 'Dal Makhani, Veg Pulao, Roti',
                'non_veg': 'Fish Curry, Steamed Rice'
            },
            'Dinner': {
                'veg': 'Chana Masala, Bhindi Fry, Roti',
                'non_veg': 'Mutton Curry, Jeera Rice'
            }
        },
        2: {  # Wednesday
            'Breakfast': {
                'veg': 'Upma, Vada, Coconut Chutney',
                'non_veg': 'Egg Bhurji, Paratha'
            },
            'Lunch': {
                'veg': 'Aloo Gobi, Dal Tadka, Roti',
                'non_veg': 'Chicken Curry, Steamed Rice'
            },
            'Dinner': {
                'veg': 'Veg Kofta Curry, Pulao, Naan',
                'non_veg': 'Chicken Tikka Masala, Butter Naan'
            }
        },
        3: {  # Thursday
            'Breakfast': {
                'veg': 'Aloo Paratha, Curd, Pickle',
                'non_veg': 'Egg Sandwich, Coffee'
            },
            'Lunch': {
                'veg': 'Kadhi Pakora, Rice, Roti',
                'non_veg': 'Chicken Do Pyaza, Jeera Rice'
            },
            'Dinner': {
                'veg': 'Malai Kofta, Pulao, Butter Naan',
                'non_veg': 'Fish Tikka, Butter Naan'
            }
        },
        4: {  # Friday
            'Breakfast': {
                'veg': 'Puri Bhaji, Tea',
                'non_veg': 'Egg Burji, Toast'
            },
            'Lunch': {
                'veg': 'Pav Bhaji, Pulao',
                'non_veg': 'Mutton Kheema, Butter Naan'
            },
            'Dinner': {
                'veg': 'Palak Paneer, Jeera Rice, Roti',
                'non_veg': 'Chicken Handi, Butter Naan'
            }
        },
        5: {  # Saturday
            'Breakfast': {
                'veg': 'Dosa, Sambar, Chutney',
                'non_veg': 'Egg Masala, Roti'
            },
            'Lunch': {
                'veg': 'Chole Bhature, Pulao',
                'non_veg': 'Butter Chicken, Naan'
            },
            'Dinner': {
                'veg': 'Veg Manchurian, Fried Rice',
                'non_veg': 'Chicken Biryani, Raita'
            }
        },
        6: {  # Sunday
            'Breakfast': {
                'veg': 'Vegetable Sandwich, Chai',
                'non_veg': 'Masala Omelette, Coffee'
            },
            'Lunch': {
                'veg': 'Veg Thali (Paneer, Dal, Rice, Roti)',
                'non_veg': 'Non-Veg Thali (Chicken, Rice, Roti)'
            },
            'Dinner': {
                'veg': 'Special Veg Pulao, Raita, Gulab Jamun',
                'non_veg': 'Special Chicken Biryani, Raita, Gulab Jamun'
            }
        }
    }
    
    try:
        # Skip if menus for this date already exist
        if MealMenu.query.filter_by(date=target_date).first():
            return
        
        day_menu = weekly_menus[weekday]
        
        # Create breakfast, lunch, dinner for this day
        breakfast = MealMenu(
            date=target_date,
            meal_type='Breakfast',
            veg_menu=day_menu['Breakfast']['veg'],
            non_veg_menu=day_menu['Breakfast']['non_veg']
        )
        
        lunch = MealMenu(
            date=target_date,
            meal_type='Lunch',
            veg_menu=day_menu['Lunch']['veg'],
            non_veg_menu=day_menu['Lunch']['non_veg']
        )
        
        dinner = MealMenu(
            date=target_date,
            meal_type='Dinner',
            veg_menu=day_menu['Dinner']['veg'],
            non_veg_menu=day_menu['Dinner']['non_veg']
        )
        
        # Add all meals for this day
        db.session.add_all([breakfast, lunch, dinner])
        db.session.commit()
        
        print(f"Created menu for {target_date.strftime('%Y-%m-%d')} ({target_date.strftime('%A')})")
    except Exception as e:
        db.session.rollback()
        print(f"Error creating menu for {target_date.strftime('%Y-%m-%d')}: {str(e)}")

@app.route('/logout')
@login_required
def logout():
    # Clear any verification tokens for this user
    for token, data in list(verification_tokens.items()):
        if data['user_id'] == current_user.id:
            del verification_tokens[token]
    
    # Log out the user
    logout_user()
    flash('You have been logged out successfully.')
    return redirect(url_for('home'))

# Error handlers
@app.errorhandler(404)
def not_found_error(error):
    return render_template('error.html', 
                         error_code=404, 
                         error_message="Page not found"), 404

@app.errorhandler(500)
def internal_error(error):
    db.session.rollback()
    return render_template('error.html', 
                         error_code=500, 
                         error_message="Internal server error"), 500

# Create default users if none exist
def create_default_users():
    with app.app_context():
        # Check if we already have users
        if User.query.count() == 0:
            # Create 20 students with predefined details
            students = [
                {
                    'name': 'Rahul Sharma',
                    'email': 'student1@gmail.com'
                },
                {
                    'name': 'Priya Patel',
                    'email': 'student2@gmail.com'
                },
                {
                    'name': 'Amit Kumar',
                    'email': 'student3@gmail.com'
                },
                {
                    'name': 'Sneha Gupta',
                    'email': 'student4@gmail.com'
                },
                {
                    'name': 'Raj Malhotra',
                    'email': 'student5@gmail.com'
                },
                {
                    'name': 'Neha Singh',
                    'email': 'student6@gmail.com'
                },
                {
                    'name': 'Arun Verma',
                    'email': 'student7@gmail.com'
                },
                {
                    'name': 'Meera Reddy',
                    'email': 'student8@gmail.com'
                },
                {
                    'name': 'Vikram Joshi',
                    'email': 'student9@gmail.com'
                },
                {
                    'name': 'Anjali Desai',
                    'email': 'student10@gmail.com'
                },
                {
                    'name': 'Karan Shah',
                    'email': 'student11@gmail.com'
                },
                {
                    'name': 'Pooja Mehta',
                    'email': 'student12@gmail.com'
                },
                {
                    'name': 'Arjun Nair',
                    'email': 'student13@gmail.com'
                },
                {
                    'name': 'Divya Kapoor',
                    'email': 'student14@gmail.com'
                },
                {
                    'name': 'Rohan Khanna',
                    'email': 'student15@gmail.com'
                },
                {
                    'name': 'Ananya Iyer',
                    'email': 'student16@gmail.com'
                },
                {
                    'name': 'Siddharth Rao',
                    'email': 'student17@gmail.com'
                },
                {
                    'name': 'Nisha Menon',
                    'email': 'student18@gmail.com'
                },
                {
                    'name': 'Gaurav Bhat',
                    'email': 'student19@gmail.com'
                },
                {
                    'name': 'Riya Saxena',
                    'email': 'student20@gmail.com'
                }
            ]

            # Add each student to the database with uniform balance
            for i, student in enumerate(students, 1):
                user = User(
                    email=student['email'],
                    name=student['name'],
                    balance_money=2000.0,  # Set uniform balance of ₹2000
                    veg_meals_count=0,
                    non_veg_meals_count=0,
                    veg_meals_spent=0.0,
                    non_veg_meals_spent=0.0
                )
                # Set password as 'passN' where N is the student number
                user.set_password(f'pass{i}')
                db.session.add(user)
            
            # Commit all changes
            try:
                db.session.commit()
                print("Successfully created 20 default student accounts with ₹2000 balance each")
            except Exception as e:
                db.session.rollback()
                print(f"Error creating default students: {str(e)}")

# Create test meal menu if none exists
def create_test_menu():
    with app.app_context():
        # Define a weekly meal schedule that repeats
        weekly_menus = {
            0: {  # Monday
                'Breakfast': {
                    'veg': 'Idli, Sambar, Coconut Chutney',
                    'non_veg': 'Egg Bhurji with Toast'
                },
                'Lunch': {
                    'veg': 'Rajma Chawal, Jeera Aloo, Roti',
                    'non_veg': 'Butter Chicken, Jeera Rice'
                },
                'Dinner': {
                    'veg': 'Paneer Butter Masala, Pulao, Naan',
                    'non_veg': 'Chicken Biryani, Raita'
                }
            },
            1: {  # Tuesday
                'Breakfast': {
                    'veg': 'Poha, Jalebi, Tea',
                    'non_veg': 'Omelette, Toast, Coffee'
                },
                'Lunch': {
                    'veg': 'Dal Makhani, Veg Pulao, Roti',
                    'non_veg': 'Fish Curry, Steamed Rice'
                },
                'Dinner': {
                    'veg': 'Chana Masala, Bhindi Fry, Roti',
                    'non_veg': 'Mutton Curry, Jeera Rice'
                }
            },
            2: {  # Wednesday
                'Breakfast': {
                    'veg': 'Upma, Vada, Coconut Chutney',
                    'non_veg': 'Egg Bhurji, Paratha'
                },
                'Lunch': {
                    'veg': 'Aloo Gobi, Dal Tadka, Roti',
                    'non_veg': 'Chicken Curry, Steamed Rice'
                },
                'Dinner': {
                    'veg': 'Veg Kofta Curry, Pulao, Naan',
                    'non_veg': 'Chicken Tikka Masala, Butter Naan'
                }
            },
            3: {  # Thursday
                'Breakfast': {
                    'veg': 'Aloo Paratha, Curd, Pickle',
                    'non_veg': 'Egg Sandwich, Coffee'
                },
                'Lunch': {
                    'veg': 'Kadhi Pakora, Rice, Roti',
                    'non_veg': 'Chicken Do Pyaza, Jeera Rice'
                },
                'Dinner': {
                    'veg': 'Malai Kofta, Pulao, Butter Naan',
                    'non_veg': 'Fish Tikka, Butter Naan'
                }
            },
            4: {  # Friday
                'Breakfast': {
                    'veg': 'Puri Bhaji, Tea',
                    'non_veg': 'Egg Burji, Toast'
                },
                'Lunch': {
                    'veg': 'Pav Bhaji, Pulao',
                    'non_veg': 'Mutton Kheema, Butter Naan'
                },
                'Dinner': {
                    'veg': 'Palak Paneer, Jeera Rice, Roti',
                    'non_veg': 'Chicken Handi, Butter Naan'
                }
            },
            5: {  # Saturday
                'Breakfast': {
                    'veg': 'Dosa, Sambar, Chutney',
                    'non_veg': 'Egg Masala, Roti'
                },
                'Lunch': {
                    'veg': 'Chole Bhature, Pulao',
                    'non_veg': 'Butter Chicken, Naan'
                },
                'Dinner': {
                    'veg': 'Veg Manchurian, Fried Rice',
                    'non_veg': 'Chicken Biryani, Raita'
                }
            },
            6: {  # Sunday
                'Breakfast': {
                    'veg': 'Vegetable Sandwich, Chai',
                    'non_veg': 'Masala Omelette, Coffee'
                },
                'Lunch': {
                    'veg': 'Veg Thali (Paneer, Dal, Rice, Roti)',
                    'non_veg': 'Non-Veg Thali (Chicken, Rice, Roti)'
                },
                'Dinner': {
                    'veg': 'Special Veg Pulao, Raita, Gulab Jamun',
                    'non_veg': 'Special Chicken Biryani, Raita, Gulab Jamun'
                }
            }
        }
        
        # Get dates for the entire week (starting from today)
        today = date.today()
        current_weekday = today.weekday()
        
        # Calculate the start of the current week (Monday)
        start_of_week = today - timedelta(days=current_weekday)
        
        # Create menus for each day of the week if they don't exist
        for day_offset in range(7):
            menu_date = start_of_week + timedelta(days=day_offset)
            weekday = menu_date.weekday()  # 0 = Monday, 6 = Sunday
            
            # Skip if menus for this date already exist
            if MealMenu.query.filter_by(date=menu_date).first():
                continue
            
            day_menu = weekly_menus[weekday]
            
            # Create breakfast, lunch, dinner for this day
            breakfast = MealMenu(
                date=menu_date,
                meal_type='Breakfast',
                veg_menu=day_menu['Breakfast']['veg'],
                non_veg_menu=day_menu['Breakfast']['non_veg']
            )
            
            lunch = MealMenu(
                date=menu_date,
                meal_type='Lunch',
                veg_menu=day_menu['Lunch']['veg'],
                non_veg_menu=day_menu['Lunch']['non_veg']
            )
            
            dinner = MealMenu(
                date=menu_date,
                meal_type='Dinner',
                veg_menu=day_menu['Dinner']['veg'],
                non_veg_menu=day_menu['Dinner']['non_veg']
            )
            
            # Add all meals for this day
            db.session.add_all([breakfast, lunch, dinner])
        
        # Also create menus for previous week for historical data
        for day_offset in range(1, 8):
            menu_date = start_of_week - timedelta(days=day_offset)
            weekday = menu_date.weekday()
            
            # Skip if menus for this date already exist
            if MealMenu.query.filter_by(date=menu_date).first():
                continue
            
            day_menu = weekly_menus[weekday]
            
            # Create breakfast, lunch, dinner for this previous day
            breakfast = MealMenu(
                date=menu_date,
                meal_type='Breakfast',
                veg_menu=day_menu['Breakfast']['veg'],
                non_veg_menu=day_menu['Breakfast']['non_veg']
            )
            
            lunch = MealMenu(
                date=menu_date,
                meal_type='Lunch',
                veg_menu=day_menu['Lunch']['veg'],
                non_veg_menu=day_menu['Lunch']['non_veg']
            )
            
            dinner = MealMenu(
                date=menu_date,
                meal_type='Dinner',
                veg_menu=day_menu['Dinner']['veg'],
                non_veg_menu=day_menu['Dinner']['non_veg']
            )
            
            # Add all meals for this previous day
            db.session.add_all([breakfast, lunch, dinner])
            
        # Create menus for next week as well
        for day_offset in range(1, 8):
            menu_date = start_of_week + timedelta(days=day_offset+6)
            weekday = menu_date.weekday()
            
            # Skip if menus for this date already exist
            if MealMenu.query.filter_by(date=menu_date).first():
                continue
            
            day_menu = weekly_menus[weekday]
            
            # Create breakfast, lunch, dinner for this next day
            breakfast = MealMenu(
                date=menu_date,
                meal_type='Breakfast',
                veg_menu=day_menu['Breakfast']['veg'],
                non_veg_menu=day_menu['Breakfast']['non_veg']
            )
            
            lunch = MealMenu(
                date=menu_date,
                meal_type='Lunch',
                veg_menu=day_menu['Lunch']['veg'],
                non_veg_menu=day_menu['Lunch']['non_veg']
            )
            
            dinner = MealMenu(
                date=menu_date,
                meal_type='Dinner',
                veg_menu=day_menu['Dinner']['veg'],
                non_veg_menu=day_menu['Dinner']['non_veg']
            )
            
            # Add all meals for this next day
            db.session.add_all([breakfast, lunch, dinner])
            
        # Commit all changes
        try:
            db.session.commit()
            print("Successfully created weekly meal menus")
        except Exception as e:
            db.session.rollback()
            print(f"Error creating weekly menus: {str(e)}")

if __name__ == '__main__':
    # Create database tables
    with app.app_context():
        db.create_all()
        create_default_users()  # Create default students
        create_test_menu()      # Create weekly meal menus
    
    # Run the application
    app.run(debug=True, host='0.0.0.0', port=5000) 
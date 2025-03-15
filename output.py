import streamlit as st
import requests
import json
import time
import random
import numpy as np
import speech_recognition as sr
from datetime import datetime
import chardet
import pytesseract
from PIL import Image
import base64
import sys
from io import StringIO
import contextlib
import sqlite3
import bcrypt
import os
import uuid

# --- Initialize database on startup ---
def init_db():
    """Initialize the SQLite database for user authentication."""
    os.makedirs("user_databases", exist_ok=True)
    conn = sqlite3.connect('user_db.sqlite')
    c = conn.cursor()
    
    # First check if the table exists
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
    table_exists = c.fetchone()
    
    if not table_exists:
        # Create the table with all required columns
        c.execute('''
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        ''')
    else:
        # Check if created_at column exists
        try:
            c.execute("SELECT created_at FROM users LIMIT 1")
        except sqlite3.OperationalError:
            # For existing tables, we need to recreate the table
            # First, get the current data
            c.execute("SELECT id, username, password FROM users")
            users_data = c.fetchall()
            
            # Create a temporary table
            c.execute("ALTER TABLE users RENAME TO users_old")
            
            # Create the new table with the created_at column
            c.execute('''
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            ''')
            
            # Copy the data, using the current timestamp for created_at
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            for user_id, username, password in users_data:
                c.execute(
                    "INSERT INTO users (id, username, password, created_at) VALUES (?, ?, ?, ?)",
                    (user_id, username, password, current_time)
                )
                
            # Drop the old table
            c.execute("DROP TABLE users_old")
    
    conn.commit()
    conn.close()

def create_user_db(username):
    """Create a separate SQLite database for each user."""
    user_db_path = f"user_databases/{username}.sqlite"
    conn = sqlite3.connect(user_db_path)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS chat_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            user_input TEXT NOT NULL,
            generated_code TEXT NOT NULL
        )
    ''')
    
    # Create user preferences table
    c.execute('''
        CREATE TABLE IF NOT EXISTS user_preferences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            temperature REAL DEFAULT 0.7,
            speed INTEGER DEFAULT 5,
            favorite_language TEXT DEFAULT 'python'
        )
    ''')
    
    # Insert default preferences
    c.execute('INSERT INTO user_preferences (temperature, speed, favorite_language) VALUES (?, ?, ?)',
              (0.7, 5, 'python'))
    
    conn.commit()
    conn.close()

# --- User Authentication Functions ---
def hash_password(password):
    """Hash a password using bcrypt."""
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
def verify_password(password, hashed_password):
    """Verify a password against its hash."""
    # Check if hashed_password is already bytes or needs to be encoded
    if isinstance(hashed_password, str):
        hashed_bytes = hashed_password.encode('utf-8')
    else:
        hashed_bytes = hashed_password
        
    # Ensure password is encoded as bytes
    password_bytes = password.encode('utf-8') if isinstance(password, str) else password
    
    return bcrypt.checkpw(password_bytes, hashed_bytes)
def register_user(username, password):
    """Register a new user."""
    conn = sqlite3.connect('user_db.sqlite')
    c = conn.cursor()
    try:
        hashed_password = hash_password(password)
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        c.execute('INSERT INTO users (username, password, created_at) VALUES (?, ?, ?)', 
                 (username, hashed_password, current_time))
        conn.commit()
        create_user_db(username)  # Create a separate database for the user
        return True, "Registration successful!"
    except sqlite3.IntegrityError:
        return False, "Username already exists."
    except Exception as e:
        return False, f"Error: {str(e)}"
    finally:
        conn.close()

def authenticate_user(username, password):
    """Authenticate a user."""
    conn = sqlite3.connect('user_db.sqlite')
    c = conn.cursor()
    c.execute('SELECT password FROM users WHERE username = ?', (username,))
    result = c.fetchone()
    conn.close()
    
    if result and verify_password(password, result[0]):
        return True, "Login successful!"
    elif result:
        return False, "Incorrect password."
    else:
        return False, "Username not found."

# --- Chat History Functions ---
def save_chat_history(username, user_input, generated_code):
    """Save chat history to the user's database."""
    user_db_path = f"user_databases/{username}.sqlite"
    conn = sqlite3.connect(user_db_path)
    c = conn.cursor()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    c.execute('INSERT INTO chat_history (timestamp, user_input, generated_code) VALUES (?, ?, ?)',
              (timestamp, user_input, generated_code))
    conn.commit()
    conn.close()

def load_chat_history(username):
    """Load chat history from the user's database."""
    user_db_path = f"user_databases/{username}.sqlite"
    if not os.path.exists(user_db_path):
        return []
    
    conn = sqlite3.connect(user_db_path)
    c = conn.cursor()
    c.execute('SELECT timestamp, user_input, generated_code FROM chat_history ORDER BY timestamp DESC LIMIT 10')
    history = c.fetchall()
    conn.close()
    return history

def get_user_preferences(username):
    """Get user preferences from the database."""
    user_db_path = f"user_databases/{username}.sqlite"
    if not os.path.exists(user_db_path):
        return {"temperature": 0.7, "speed": 5, "favorite_language": "python"}
    
    conn = sqlite3.connect(user_db_path)
    c = conn.cursor()
    c.execute('SELECT temperature, speed, favorite_language FROM user_preferences LIMIT 1')
    result = c.fetchone()
    conn.close()
    
    if result:
        return {"temperature": result[0], "speed": result[1], "favorite_language": result[2]}
    else:
        return {"temperature": 0.7, "speed": 5, "favorite_language": "python"}

def update_user_preferences(username, preferences):
    """Update user preferences in the database."""
    user_db_path = f"user_databases/{username}.sqlite"
    conn = sqlite3.connect(user_db_path)
    c = conn.cursor()
    c.execute('''
        UPDATE user_preferences 
        SET temperature = ?, speed = ?, favorite_language = ?
        WHERE id = 1
    ''', (preferences["temperature"], preferences["speed"], preferences["favorite_language"]))
    conn.commit()
    conn.close()

# --- Initialize Session State Variables ---
def init_session_state():
    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []
    if "generated_code" not in st.session_state:
        st.session_state.generated_code = ""
    if "temperature" not in st.session_state:
        st.session_state.temperature = 0.7
    if "speed" not in st.session_state:
        st.session_state.speed = 5
    if "user_input_buffer" not in st.session_state:
        st.session_state.user_input_buffer = ""
    if "feedback_submitted" not in st.session_state:
        st.session_state.feedback_submitted = False
    if "selected_optimization" not in st.session_state:
        st.session_state.selected_optimization = None
    if "recognized_text" not in st.session_state:
        st.session_state.recognized_text = ""
    if "input_text_buffer" not in st.session_state:
        st.session_state.input_text_buffer = ""
    if "should_update_textarea" not in st.session_state:
        st.session_state.should_update_textarea = False
    if "feedback_score" not in st.session_state:
        st.session_state.feedback_score = 0
    if "last_action_idx" not in st.session_state:
        st.session_state.last_action_idx = 0
    if "code_language" not in st.session_state:
        st.session_state.code_language = "python"
    if "dark_mode" not in st.session_state:
        st.session_state.dark_mode = True  # Always set to True for dark mode only
    if "auto_submit" not in st.session_state:
        st.session_state.auto_submit = False
    
    # Authentication state variables
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False
    if "username" not in st.session_state:
        st.session_state.username = ""
    if "login_error" not in st.session_state:
        st.session_state.login_error = ""
    if "register_error" not in st.session_state:
        st.session_state.register_error = ""
    if "session_id" not in st.session_state:
        st.session_state.session_id = str(uuid.uuid4())


# --- Initialize callback state handlers ---
def init_callback_handlers():
    if "python_clicked" not in st.session_state:
        st.session_state.python_clicked = False
    if "javascript_clicked" not in st.session_state:
        st.session_state.javascript_clicked = False
    if "java_clicked" not in st.session_state:
        st.session_state.java_clicked = False
    if "cpp_clicked" not in st.session_state:
        st.session_state.cpp_clicked = False
    if "needs_rerun" not in st.session_state:
        st.session_state.needs_rerun = False

# --- Set up Tesseract OCR path (change according to your installation) ---
pytesseract.pytesseract.tesseract_cmd = r"D:\tesseract\tesseract.exe"

# --- API Endpoint ---
OLLAMA_URL =  "https://api.runpod.ai/v2/eqon1clbu33dw9/run/api/generate"


# --- Function to execute Python code ---
def execute_python_code(code_to_execute):
    """
    Safely execute Python code and capture its output.
    Returns a tuple of (stdout_output, stderr_output, execution_successful)
    """
    # Create string buffers to capture stdout and stderr
    stdout_buffer = StringIO()
    stderr_buffer = StringIO()
    execution_successful = True

    # Redirect stdout and stderr to our buffers
    with contextlib.redirect_stdout(stdout_buffer), contextlib.redirect_stderr(stderr_buffer):
        try:
            # Create a local namespace for execution
            local_namespace = {}

            # Execute the code
            exec(code_to_execute, {}, local_namespace)

        except Exception as e:
            # Capture any exceptions
            print(f"Error: {str(e)}", file=stderr_buffer)
            execution_successful = False

    # Get the captured output
    stdout_output = stdout_buffer.getvalue()
    stderr_output = stderr_buffer.getvalue()

    return stdout_output, stderr_output, execution_successful

# --- Reinforcement Learning Setup ---
actions = [
    "Generate basic code for: {}",
    "Generate structured code with functions and error handling for: {}",
    "Generate code for: {} and add detailed comments for clarity",
    "Generate optimized code for: {}"
]
num_actions = len(actions)

def initialize_q_table():
    if "Q_table" not in st.session_state:
        st.session_state.Q_table = np.zeros(num_actions)

def get_action(Q_table, epsilon=0.1):
    """Epsilon-greedy action selection."""
    if random.uniform(0, 1) < epsilon:
        return random.choice(range(num_actions))
    else:
        return int(np.argmax(Q_table))

def update_Q(Q_table, action_idx, reward, learning_rate=0.1, discount_factor=0.9):
    """Update Q-value for the given action."""
    best_next = np.max(Q_table)
    Q_table[action_idx] += learning_rate * (reward + discount_factor * best_next - Q_table[action_idx])
    return Q_table

# --- Custom Styling ---
def get_base64_encoded_image(image_path):
    with open(image_path, "rb") as img_file:
        return base64.b64encode(img_file.read()).decode()

# Custom CSS for dark mode only
def get_dark_mode_css():
    return """
    /* Base application styling */
    .stApp {
        background-color: #0A0A0A;
        color: #F8F8F8;
        font-family: 'SF Pro Display', -apple-system, BlinkMacSystemFont, sans-serif;
    }

    /* Premium button styling */
    .stButton button { 
        background-color: #111111; 
        color: #FFFFFF; 
        font-size: 13px; 
        padding: 10px 18px; 
        border-radius: 3px; 
        border: 1px solid #222222; 
        transition: all 0.2s cubic-bezier(0.25, 0.8, 0.25, 1);
        box-shadow: 0 1px 3px rgba(0,0,0,0.12), 0 1px 2px rgba(0,0,0,0.24);
        font-weight: 500;
        text-transform: uppercase;
        letter-spacing: 0.8px;
        margin: 0.25rem 0;
        position: relative;
        overflow: hidden;
    }

    .stButton button:hover { 
        background-color: #191919; 
        border-color: #333333;
        box-shadow: 0 3px 6px rgba(0,0,0,0.16), 0 3px 6px rgba(0,0,0,0.23);
        transform: translateY(-1px);
    }

    .stButton button:active {
        background-color: #222222;
        transform: translateY(1px);
        box-shadow: 0 1px 2px rgba(0,0,0,0.15);
    }

    /* Button special effects */
    .stButton button::after {
        content: '';
        position: absolute;
        top: 50%;
        left: 50%;
        width: 5px;
        height: 5px;
        background: rgba(255, 255, 255, 0.1);
        opacity: 0;
        border-radius: 100%;
        transform: scale(1, 1) translate(-50%);
        transform-origin: 50% 50%;
    }

    .stButton button:focus:not(:active)::after {
        animation: ripple 1s ease-out;
    }

    @keyframes ripple {
        0% {
            transform: scale(0, 0);
            opacity: 0.5;
        }
        20% {
            transform: scale(25, 25);
            opacity: 0.3;
        }
        100% {
            opacity: 0;
            transform: scale(40, 40);
        }
    }

    /* Premium text area styling */
    .stTextArea textarea { 
        background-color: #111111; 
        color: #F8F8F8; 
        height: 68px; 
        border: 1px solid #222222; 
        border-radius: 3px;
        padding: 12px;
        font-family: 'SF Mono', SFMono-Regular, Consolas, 'Liberation Mono', Menlo, monospace;
        font-size: 14px;
        resize: none;
        transition: border-color 0.2s ease-in-out;
    }

    .stTextArea textarea:focus {
        border-color: #444444;
        box-shadow: none;
        outline: none;
    }

    /* Login form styling */
    .login-container {
        background-color: #111111;
        padding: 20px;
        border-radius: 5px;
        border: 1px solid #222222;
        margin-bottom: 20px;
    }

    .login-header {
        text-align: center;
        margin-bottom: 20px;
        color: #FFFFFF;
        font-size: 24px;
    }
    .login-input {
    margin-bottom: 10px;
    background-color: #111111;
    border: 1px solid #222222;
    border-radius: 3px;
    padding: 10px;
    color: #F8F8F8;
    width: 100%;
    font-size: 14px;
    transition: border-color 0.2s ease-in-out;
}

.login-input:focus {
    border-color: #444444;
    box-shadow: none;
    outline: none;
}

.login-button {
    background-color: #111111;
    color: #FFFFFF;
    border: 1px solid #222222;
    border-radius: 3px;
    padding: 10px 15px;
    width: 100%;
    margin-top: 15px;
    font-weight: 500;
    cursor: pointer;
    transition: all 0.2s ease;
}

.login-button:hover {
    background-color: #191919;
    border-color: #333333;
}
/* Sidebar enhancements */
    .sidebar .sidebar-content { 
        background-color: #050505 !important; 
        color: #F8F8F8 !important;
        border-right: 1px solid #1A1A1A;
    }

    /* Structure elements */
    .css-1544g2n { 
        padding: 2rem 1rem; 
        background-color: #0A0A0A !important; 
    }

    .css-18e3th9 { 
        padding: 1rem 1rem 10rem; 
        background-color: #0A0A0A !important; 
    }

    /* Fix for chat history container */
    [data-testid="stSidebar"] {
        background-color: #050505 !important;
        color: #F8F8F8 !important;
    }

    /* Chat history specific styling */
    .sidebar .sidebar-content * {
        background-color: #050505 !important;
        color: #F8F8F8 !important;
    }

    .sidebar .block-container {
        background-color: #050505 !important;
    }

    /* Style all divs within the sidebar */
    .sidebar div {
        background-color: #050505 !important;
        color: #F8F8F8 !important;
    }

    /* Style the chat messages in the history */
    .sidebar div[data-testid="stMarkdown"] {
        background-color: #111111 !important;
        border-radius: 3px;
        margin-bottom: 8px;
        padding: 8px;
        border: 1px solid #222222;
    }

    /* Style markdown content within the sidebar */
    .sidebar div[data-testid="stMarkdown"] p {
        color: #F8F8F8 !important;
    }

    /* Additional chat styling for HTML-based chat bubbles */
    .sidebar div[style*="background-color: #3B3B3B"],
    .sidebar div[style*="background-color: #2D2D2D"] {
        background-color: #111111 !important;
        border: 1px solid #222222 !important;
        border-radius: 3px !important;
        margin: 5px 0 !important;
        box-shadow: 0 1px 2px rgba(0,0,0,0.15) !important;
    }

    /* Login form styles */
    .auth-form {
        background-color: #111111;
        border-radius: 5px;
        padding: 20px;
        border: 1px solid #222222;
        margin-bottom: 20px;
    }

    .auth-tab {
        margin-bottom: 15px;
    }

    /* Code styling */
    code { 
        background-color: #111111; 
        color: #F8F8F8; 
        border-radius: 3px;
        padding: 2px 5px;
        font-family: 'SF Mono', SFMono-Regular, Consolas, 'Liberation Mono', Menlo, monospace;
    }

    /* Code blocks inside chat history */
    .sidebar pre code {
        background-color: #0A0A0A !important;
        color: #F8F8F8 !important;
        padding: 8px;
        border-radius: 3px;
        border: 1px solid #222222;
        font-size: 12px;
    }

    /* Component containers */
    .highlight { 
        background-color: #111111; 
        border-radius: 3px; 
        padding: 12px; 
        border: 1px solid #222222;
        margin: 10px 0;
    }

    .card { 
        background-color: #111111; 
        border-radius: 3px; 
        padding: 16px; 
        margin-bottom: 16px; 
        border: 1px solid #222222;
        box-shadow: 0 1px 3px rgba(0,0,0,0.12), 0 1px 2px rgba(0,0,0,0.24);
        transition: all 0.3s cubic-bezier(.25,.8,.25,1);
    }

    .card:hover {
        box-shadow: 0 3px 6px rgba(0,0,0,0.16), 0 3px 6px rgba(0,0,0,0.23);
    }

    /* Form controls */
    .stSelectbox div[data-baseweb="select"] {
        background-color: #111111;
        border-color: #222222;
        border-radius: 3px;
        transition: border-color 0.2s ease;
    }

    .stSelectbox div[data-baseweb="select"]:hover {
        border-color: #333333;
    }

    /* Profile badge */
    .profile-badge {
        background-color: #222222;
        border-radius: 50px;
        padding: 5px 15px;
        display: inline-flex;
        align-items: center;
        gap: 8px;
        margin-bottom: 10px;
    }

    .profile-badge img {
        width: 24px;
        height: 24px;
        border-radius: 50%;
    }
    """

# --- Speech Recognition ---
def recognize_speech():
    recognizer = sr.Recognizer()
    try:
        with sr.Microphone() as source:
            st.info("Listening... Speak now.")
            audio = recognizer.listen(source, timeout=5)

        text = recognizer.recognize_google(audio)
        return text
    except sr.UnknownValueError:
        st.error("Could not understand the audio. Please try again.")
        return None
    except sr.RequestError:
        st.error("Could not connect to recognition service. Check your internet.")
        return None
    except Exception as e:
        st.error(f"Error during speech recognition: {str(e)}")
        return None

# --- Image Processing ---
def process_image(image):
    try:
        image = Image.open(image)
        text = pytesseract.image_to_string(image)
        return text
    except Exception as e:
        st.error(f"Error processing image: {str(e)}")
        return None

# --- Document Processing ---
def process_document(doc_file):
    try:
        raw_data = doc_file.read()
        encoding_detected = chardet.detect(raw_data)['encoding']
        content = raw_data.decode(encoding_detected or "utf-8", errors="ignore")
        return content
    except Exception as e:
        st.error(f"Error processing document: {str(e)}")
        return None

# --- Update Input Buffer ---
def update_input_buffer(text, prefix=""):
    if text:
        st.session_state.input_text_buffer = f"{prefix}{text}"
        st.session_state.should_update_textarea = True
        st.session_state.needs_rerun = True  # Flag for rerun after execution of callback
        # Optionally auto-submit the form to immediately generate code
        st.session_state.auto_submit = True  # Add this flag

# --- Detect Code Language ---
def detect_language(code):
    """Simple heuristic to guess the programming language."""
    code = code.lower()
    if "def " in code or "import " in code or "print(" in code:
        return "python"
    elif "function" in code or "var " in code or "const " in code or "let " in code or "console.log" in code:
        return "javascript"
    elif "public class" in code or "void main" in code or "System.out.println" in code:
        return "java"
    elif "cout <<" in code or "#include" in code or "int main" in code:
        return "cpp"
    else:
        return "python"  # Default

# --- Auth related callback functions ---
def login_callback():
    username = st.session_state.login_username
    password = st.session_state.login_password
    success, message = authenticate_user(username, password)
    
    if success:
        st.session_state.authenticated = True
        st.session_state.username = username
        st.session_state.login_error = ""
        
        # Load user preferences
        preferences = get_user_preferences(username)
        st.session_state.temperature = preferences["temperature"]
        st.session_state.speed = preferences["speed"]
        st.session_state.code_language = preferences["favorite_language"]
        
        # Load user chat history
        history = load_chat_history(username)
        st.session_state.chat_history = []
        for timestamp, user_input, generated_code in history:
            st.session_state.chat_history.append(f"""
            <div style='background-color: #3B3B3B; 
                 padding: 10px; border-radius: 5px; margin: 5px 0;'>
                <strong>You:</strong> <small>({timestamp})</small><br>{user_input}
            </div>
            """)
            st.session_state.chat_history.append(f"""
            <div style='background-color: #2D2D2D; 
                 padding: 10px; border-radius: 5px; margin: 5px 0;'>
                <strong>Coderzz.AI:</strong> <small>({timestamp})</small><br>
                <pre><code>{generated_code}</code></pre>
            </div>
            """)
    else:
        st.session_state.login_error = message

def register_callback():
    username = st.session_state.register_username
    password = st.session_state.register_password
    confirm_password = st.session_state.register_confirm_password
    
    if password != confirm_password:
        st.session_state.register_error = "Passwords do not match."
        return
    
    if len(password) < 6:
        st.session_state.register_error = "Password must be at least 6 characters long."
        return
        
    success, message = register_user(username, password)
    
    if success:
        st.session_state.authenticated = True
        st.session_state.username = username
        st.session_state.register_error = ""
    else:
        st.session_state.register_error = message

def logout_callback():
    # Save preferences before logout
    if st.session_state.authenticated and st.session_state.username:
        preferences = {
            "temperature": st.session_state.temperature,
            "speed": st.session_state.speed,
            "favorite_language": st.session_state.code_language
        }
        update_user_preferences(st.session_state.username, preferences)
    
    # Reset all session state
    for key in list(st.session_state.keys()):
        if key != "session_id":  # Keep session ID for tracking purposes
            del st.session_state[key]
            
    # Reinitialize session state
    init_session_state()
    init_callback_handlers()
    initialize_q_table()

# --- Main App Logic ---
def main():
    # Initialize the database if it doesn't exist
    init_db()
    
    # Initialize session state
    init_session_state()
    init_callback_handlers()
    initialize_q_table()
    
    # Set up page config
    st.set_page_config(page_title="֎🇦🇮 Coderzz.AI - AI Coding Assistant", layout="wide")
    
    # Apply custom CSS
    st.markdown(
        f"""
        <style>
        {get_dark_mode_css()}
        </style>
        """,
        unsafe_allow_html=True
    )

    # Authentication UI
    if not st.session_state.authenticated:
        display_auth_ui()
    else:
        display_main_app()
        
    # Handle rerun flag
    if st.session_state.needs_rerun:
        st.session_state.needs_rerun = False
        st.rerun()

def display_auth_ui():
    # Center-aligned container for authentication
    col1, col2, col3 = st.columns([1, 2, 1])
    
    with col2:
        st.title(" 🤖 Coderzz.AI")
        st.markdown("#### Your AI Coding Assistant")
        
        # Auth container
        st.markdown('<div class="auth-form">', unsafe_allow_html=True)
        
        # Tabs for login and register
        login_tab, register_tab = st.tabs(["Login", "Register"])
        
        with login_tab:
            st.text_input("Username", key="login_username")
            st.text_input("Password", type="password", key="login_password")
            
            if st.button("Login", on_click=login_callback):
                pass
                
            if st.session_state.login_error:
                st.error(st.session_state.login_error)
                
        with register_tab:
            st.text_input("Username", key="register_username")
            st.text_input("Password", type="password", key="register_password")
            st.text_input("Confirm Password", type="password", key="register_confirm_password")
            
            if st.button("Register", on_click=register_callback):
                pass
                
            if st.session_state.register_error:
                st.error(st.session_state.register_error)
        
        st.markdown('</div>', unsafe_allow_html=True)
        
        # Footer
        st.markdown("---")
        st.markdown("Made with ❤ by Coderzz.AI | © 2025")

def display_main_app():
    # --- Sidebar ---
    with st.sidebar:
        st.title("🤖 Coderzz.AI")
        
        # User profile section
        st.markdown(f"""
        <div class="profile-badge">
            <span>🙍‍♂️</span> <strong>{st.session_state.username}</strong>
        </div>
        """, unsafe_allow_html=True)
        
        # Logout button
        if st.button("↩️ Logout", on_click=logout_callback):
            pass

        # Settings expander
        with st.expander("⚙ Settings"):
            st.slider("AI Temperature", min_value=0.1, max_value=1.0, value=st.session_state.temperature, step=0.1, key="temperature")
            st.slider("Code Generation Speed", min_value=1, max_value=10, value=st.session_state.speed, key="speed")

        # Metrics display - for gamification
        with st.expander("📊 Your Statistics"):
            st.metric("Codes Generated", len(st.session_state.chat_history) // 2)
            st.metric("Feedback Score", st.session_state.feedback_score)

        # Store chat history
        st.subheader("💬 Chat History")
        if st.button("🗑 Clear History"):
            st.session_state.chat_history = []
            st.session_state.needs_rerun = True  # Flag for rerun

        chat_container = st.container()
        with chat_container:
            for message in reversed(st.session_state.chat_history[-10:]):
                st.markdown(message, unsafe_allow_html=True)

    # --- Main UI ---
    st.title("💻 Coderzz.AI - Your AI Coding Assistant")

    # Intro card with animated welcome
    welcome_card = st.container()
    with welcome_card:
     st.markdown(f"""
    <div class="card">
        <h3>Welcome back, {st.session_state.username}! 👋</h3>
        <p>I can help you generate code in various programming languages. Just tell me what you need!</p>
        <p>Use the buttons below to select a language or just type your request.</p>
    </div>
    """, unsafe_allow_html=True)

   
    
    

    # Input section
    st.markdown('<div class="highlight">', unsafe_allow_html=True)
    
    # Different input methods
    input_method = st.radio("Input method:", 
                            ["Text", "Voice", "Image", "Document"], 
                            horizontal=True)
    
    # Handle the different input methods
    if input_method == "Text":
        # Text area for code requests
        text_input = st.text_area("What code would you like me to generate?", 
                                 key="text_input", 
                                 value=st.session_state.input_text_buffer if st.session_state.should_update_textarea else "",
                                 height=150)
        
        # Reset flag after updating
        if st.session_state.should_update_textarea:
            st.session_state.should_update_textarea = False
            
    elif input_method == "Voice":
        if st.button("🎤 Start Voice Recognition"):
            text = recognize_speech()
            if text:
                update_input_buffer(text, "Generate code for: ")
                
    elif input_method == "Image":
        uploaded_image = st.file_uploader("Upload an image with code or instructions", type=["jpg", "jpeg", "png"])
        if uploaded_image is not None:
            text = process_image(uploaded_image)
            if text:
                update_input_buffer(text)
                
    elif input_method == "Document":
        uploaded_doc = st.file_uploader("Upload a document with code or instructions", type=["txt", "py", "js", "java", "cpp", "c", "html", "css"])
        if uploaded_doc is not None:
            text = process_document(uploaded_doc)
            if text:
                update_input_buffer(text)
    
    # Submit button
    if st.button("Generate Code") or st.session_state.auto_submit:
        st.session_state.auto_submit = False  # Reset flag
        
        # Default to empty string if not Text input method
        user_input = ""
        if input_method == "Text" and text_input:
            user_input = text_input
        elif st.session_state.input_text_buffer:
            user_input = st.session_state.input_text_buffer
            
            # Use reinforcement learning to select the best action template
        if user_input:
            action_idx = get_action(st.session_state.Q_table)
            st.session_state.last_action_idx = action_idx
            
            # Format the prompt with the action template
            prompt = actions[action_idx].format(user_input)
            
            # Display a spinner while generating code
            with st.spinner("Generating code..."):
                try:
                    # Call model API
                    response = requests.post(
                        OLLAMA_URL,
                        json={
                            "model": "coderzz.ai",
                            "prompt": f"Generate {st.session_state.code_language} code for: {prompt}. Only return code with proper formatting and comments. Do not include explanations outside of code comments.",
                            "stream": False,
                            "temperature": st.session_state.temperature
                        },
                        timeout=30
                    )
                    
                    if response.status_code == 200:
                        # Extract generated code
                        generated_code = response.json().get("response", "").strip()
                        
                        # Only keep the code part (remove explanations)
                        if "```" in generated_code:
                            code_blocks = generated_code.split("```")
                            generated_code = "```" + code_blocks[1] + "```"
                        
                        # If language isn't specified in the user input, try to detect it
                        if "code_language" not in st.session_state:
                            st.session_state.code_language = detect_language(generated_code)
                        
                        # Store the generated code
                        st.session_state.generated_code = generated_code
                        
                        # Add to chat history
                        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        st.session_state.chat_history.append(f"""
                        <div style='background-color: #3B3B3B; 
                             padding: 10px; border-radius: 5px; margin: 5px 0;'>
                            <strong>You:</strong> <small>({timestamp})</small><br>{user_input}
                        </div>
                        """)
                        st.session_state.chat_history.append(f"""
                        <div style='background-color: #2D2D2D; 
                             padding: 10px; border-radius: 5px; margin: 5px 0;'>
                            <strong>Coderzz.AI:</strong> <small>({timestamp})</small><br>
                            <pre><code>{generated_code}</code></pre>
                        </div>
                        """)
                        
                        # Save to database if authenticated
                        if st.session_state.authenticated:
                            save_chat_history(st.session_state.username, user_input, generated_code)
                        
                    else:
                        st.error(f"Error: {response.status_code} - {response.text}")
                except Exception as e:
                    st.error(f"Error generating code: {str(e)}")
            
            # Force rerun to update UI
            st.session_state.needs_rerun = True
    
    st.markdown('</div>', unsafe_allow_html=True)
    
    # Results section
    if st.session_state.generated_code:
        st.markdown('<div class="highlight">', unsafe_allow_html=True)
        st.subheader("Generated Code")
        st.code(st.session_state.generated_code, language=st.session_state.code_language)
        
        # Copy to clipboard button
        if st.button("📋 Copy Code"):
            # Using JS to copy to clipboard (this is a placeholder, needs to be implemented with components)
            st.success("Code copied to clipboard!")
        
        # For Python code, add an execute button
        if st.session_state.code_language == "python":
            if st.button("▶️ Execute Code"):
                code_to_run = st.session_state.generated_code
                
                # Clean the code - remove markdown code blocks if present
                if "```python" in code_to_run:
                    code_to_run = code_to_run.split("```python")[1].split("```")[0]
                elif "```" in code_to_run:
                    code_to_run = code_to_run.split("```")[1].split("```")[0]
                
                # Execute the code
                stdout, stderr, execution_successful = execute_python_code(code_to_run)
                
                if execution_successful:
                    st.success("Code executed successfully!")
                    if stdout:
                        st.subheader("Output:")
                        st.code(stdout)
                else:
                    st.error("Execution failed!")
                    if stderr:
                        st.subheader("Error:")
                        st.code(stderr)
        
        # Feedback section
        st.subheader("Provide Feedback")
        feedback_col1, feedback_col2, feedback_col3, feedback_col4, feedback_col5 = st.columns(5)
        
        with feedback_col1:
            if st.button("😞 Poor"):
                st.session_state.feedback_score -= 1
                update_Q(st.session_state.Q_table, st.session_state.last_action_idx, -1)
                st.session_state.feedback_submitted = True
                st.success("Thank you for your feedback!i will improve myself")
        with feedback_col2:
            if st.button("😐 Neutral"):
                update_Q(st.session_state.Q_table, st.session_state.last_action_idx, 0)
                st.session_state.feedback_submitted = True
                st.success("Thank you for your feedback!i will better next time")
        with feedback_col3:
            if st.button("🙂 Good"):
                st.session_state.feedback_score += 1
                update_Q(st.session_state.Q_table, st.session_state.last_action_idx, 1)
                st.session_state.feedback_submitted = True
                st.success("Thank you for your feedback!i will change some drawbacks")
        with feedback_col4:
            if st.button("🤩 Excellent"):
                st.session_state.feedback_score += 2
                update_Q(st.session_state.Q_table, st.session_state.last_action_idx, 2)
                st.session_state.feedback_submitted = True
                st.success("Thank you for your feedback!💯")
    # Footer
    st.markdown("---")
    st.markdown("Made with ❤ by Coderzz.AI | © 2025")

if __name__ == "__main__":
    main()

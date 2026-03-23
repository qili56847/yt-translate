"""用户认证模块 —— Supabase PostgreSQL + Flask-Login"""

import re
from functools import wraps

from flask import Blueprint, render_template, request, redirect, url_for, jsonify
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from supabase import create_client

from config import SUPABASE_URL, SUPABASE_KEY, ADMIN_USERNAME, ADMIN_PASSWORD

auth_bp = Blueprint("auth", __name__)
login_manager = LoginManager()

# Supabase 客户端
_supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ─── 用户名/密码规则 ───
USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{3,20}$")
MIN_PASSWORD_LEN = 8
PASSWORD_RE = re.compile(r"^(?=.*[a-zA-Z])(?=.*\d).{8,}$")


class User(UserMixin):
    def __init__(self, id, username, password_hash, is_admin, created_at):
        self.id = id
        self.username = username
        self.password_hash = password_hash
        self.is_admin = bool(is_admin)
        self.created_at = created_at

    @staticmethod
    def _from_row(row):
        if not row:
            return None
        return User(
            id=row["id"],
            username=row["username"],
            password_hash=row["password_hash"],
            is_admin=row["is_admin"],
            created_at=row["created_at"],
        )

    @staticmethod
    def get_by_id(user_id):
        resp = _supabase.table("users").select("*").eq("id", user_id).execute()
        if resp.data:
            return User._from_row(resp.data[0])
        return None

    @staticmethod
    def get_by_username(username):
        resp = _supabase.table("users").select("*").eq("username", username).execute()
        if resp.data:
            return User._from_row(resp.data[0])
        return None

    @staticmethod
    def create(username, password, is_admin=False):
        _supabase.table("users").insert({
            "username": username,
            "password_hash": generate_password_hash(password),
            "is_admin": is_admin,
        }).execute()

    @staticmethod
    def delete(user_id):
        _supabase.table("users").delete().eq("id", user_id).execute()

    @staticmethod
    def list_all():
        resp = _supabase.table("users").select("*").order("id").execute()
        return [User._from_row(r) for r in resp.data]

    @staticmethod
    def update_password(user_id, new_password):
        _supabase.table("users").update({
            "password_hash": generate_password_hash(new_password),
        }).eq("id", user_id).execute()

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


@login_manager.user_loader
def load_user(user_id):
    return User.get_by_id(int(user_id))


def admin_required(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not current_user.is_admin:
            return jsonify({"error": "Forbidden"}), 403
        return f(*args, **kwargs)
    return decorated


def init_db(app):
    """初始化 Flask-Login，自动创建管理员"""
    # 检查是否有管理员用户
    resp = _supabase.table("users").select("id").eq("is_admin", True).execute()
    if not resp.data:
        User.create(ADMIN_USERNAME, ADMIN_PASSWORD, is_admin=True)
        print(f"[Auth] Admin user '{ADMIN_USERNAME}' created with default password.")
        print("[Auth] WARNING: Change the admin password via ADMIN_USERNAME/ADMIN_PASSWORD env vars!")

    login_manager.init_app(app)
    login_manager.login_view = "auth.login"

    @login_manager.unauthorized_handler
    def unauthorized():
        if request.path.startswith("/api/"):
            return jsonify({"error": "Authentication required"}), 401
        return redirect(url_for("auth.login"))

    app.register_blueprint(auth_bp)


# ─── Routes ───

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = User.get_by_username(username)
        if user and user.check_password(password):
            login_user(user)
            next_page = request.args.get("next")
            return redirect(next_page or url_for("index"))
        error = "Invalid username or password"
    return render_template("auth.html", mode="login", error=error)


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")

        if not USERNAME_RE.match(username):
            error = "Username must be 3-20 characters (letters, digits, underscore)"
        elif not PASSWORD_RE.match(password):
            error = "Password must be at least 8 characters with both letters and digits"
        elif password != confirm:
            error = "Passwords do not match"
        elif User.get_by_username(username):
            error = "Username already exists"
        else:
            User.create(username, password)
            return redirect(url_for("auth.login", registered=1))
    return render_template("auth.html", mode="register", error=error)


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))


@auth_bp.route("/admin")
@admin_required
def admin_panel():
    users = User.list_all()
    return render_template("admin.html", users=users)


@auth_bp.route("/admin/delete/<int:user_id>", methods=["POST"])
@admin_required
def delete_user(user_id):
    user = User.get_by_id(user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404
    if user.is_admin:
        return jsonify({"error": "Cannot delete admin user"}), 400
    User.delete(user_id)
    return redirect(url_for("auth.admin_panel"))


@auth_bp.route("/admin/reset-password/<int:user_id>", methods=["POST"])
@admin_required
def admin_reset_password(user_id):
    user = User.get_by_id(user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404
    new_password = request.form.get("new_password", "").strip()
    if not PASSWORD_RE.match(new_password):
        return redirect(url_for("auth.admin_panel", error="password_invalid"))
    User.update_password(user_id, new_password)
    return redirect(url_for("auth.admin_panel", reset="ok"))


@auth_bp.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    error = None
    success = False
    if request.method == "POST":
        old_password = request.form.get("old_password", "")
        new_password = request.form.get("new_password", "")
        confirm = request.form.get("confirm", "")

        if not current_user.check_password(old_password):
            error = "Current password is incorrect"
        elif not PASSWORD_RE.match(new_password):
            error = "New password must be at least 8 characters with both letters and digits"
        elif new_password != confirm:
            error = "New passwords do not match"
        else:
            User.update_password(current_user.id, new_password)
            success = True
    return render_template("change_password.html", error=error, success=success)

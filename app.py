"""
🎬 YouKo Backend - YouTube-like Platform
Using psycopg3, Supabase Storage, Flask
"""

import os
import uuid
import base64
import hashlib
from datetime import datetime
from flask import Flask, request, jsonify, session
from flask_cors import CORS
import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'youko-secret-2024-change-this')
CORS(app, supports_credentials=True)

DATABASE_URL = os.getenv('DATABASE_URL')
if not DATABASE_URL:
    raise Exception("DATABASE_URL not set!")

SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')
supabase: Client = None
if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
else:
    print("WARNING: Supabase not configured. File uploads will fail.")

def db():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)

def hash_pw(p):
    return hashlib.sha256(p.encode()).hexdigest()

def current_user():
    uid = session.get('user_id')
    if not uid:
        return None
    with db() as conn:
        return conn.execute("SELECT * FROM users WHERE id=%s", (uid,)).fetchone()

def upload_file(file_data, filename, bucket='videos'):
    if not supabase:
        return None
    try:
        if hasattr(file_data, 'read'):
            data = file_data.read()
        elif isinstance(file_data, str):
            if ',' in file_data:
                file_data = file_data.split(',')[1]
            data = base64.b64decode(file_data)
        else:
            data = file_data

        ext = filename.rsplit('.', 1)[-1] if '.' in filename else 'mp4'
        unique_name = f"{uuid.uuid4()}.{ext}"

        content_types = {
            'mp4': 'video/mp4', 'webm': 'video/webm', 'mov': 'video/quicktime',
            'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png', 'gif': 'image/gif', 'webp': 'image/webp'
        }
        ct = content_types.get(ext.lower(), 'application/octet-stream')

        supabase.storage.from_(bucket).upload(
            path=unique_name, file=data, file_options={"content-type": ct}
        )
        return supabase.storage.from_(bucket).get_public_url(unique_name)
    except Exception as e:
        print(f"Upload error: {e}")
        return None

def fmt_video(v, uid=None):
    if not v:
        return None
    d = dict(v)
    if isinstance(d.get('created_at'), datetime):
        d['created'] = int(d['created_at'].timestamp())
    d['created_at'] = d.get('created')
    return d

# ─── AUTH ───

@app.route('/api/register', methods=['POST'])
def register():
    data = request.json
    username = (data.get('username') or '').strip()
    display_name = (data.get('display_name') or '').strip()
    password = data.get('password') or ''

    if not username or not password or not display_name:
        return jsonify({'error': 'همه فیلدها لازمند'}), 400
    if len(username) < 3:
        return jsonify({'error': 'نام کاربری حداقل ۳ کاراکتر'}), 400
    if len(password) < 6:
        return jsonify({'error': 'رمز حداقل ۶ کاراکتر'}), 400

    with db() as conn:
        if conn.execute("SELECT id FROM users WHERE LOWER(username)=LOWER(%s)", (username,)).fetchone():
            return jsonify({'error': 'این نام کاربری قبلاً ثبت شده'}), 400
        user = conn.execute(
            "INSERT INTO users (username, display_name, password) VALUES (%s,%s,%s) RETURNING *",
            (username, display_name, hash_pw(password))
        ).fetchone()
        conn.commit()
        session['user_id'] = user['id']
        u = dict(user)
        del u['password']
        return jsonify({'success': True, 'user': u})

@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''

    with db() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE LOWER(username)=LOWER(%s)", (username,)
        ).fetchone()
        if not user or user['password'] != hash_pw(password):
            return jsonify({'error': 'نام کاربری یا رمز اشتباه است'}), 401
        session['user_id'] = user['id']
        u = dict(user)
        del u['password']
        return jsonify({'success': True, 'user': u})

@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'success': True})

@app.route('/api/me', methods=['GET'])
def me():
    uid = session.get('user_id')
    if not uid:
        return jsonify({'user': None})
    with db() as conn:
        user = conn.execute("SELECT * FROM users WHERE id=%s", (uid,)).fetchone()
        if not user:
            return jsonify({'user': None})
        u = dict(user)
        del u['password']
        unread = conn.execute(
            "SELECT COUNT(*) as c FROM notifications WHERE user_id=%s AND read=FALSE", (uid,)
        ).fetchone()['c']
        u['unread'] = unread
        return jsonify({'user': u, 'unread': unread})

# ─── UPLOAD ───

@app.route('/api/upload', methods=['POST'])
def upload_video():
    uid = session.get('user_id')
    if not uid:
        return jsonify({'error': 'لطفاً وارد شوید'}), 401

    title = (request.form.get('title') or '').strip()
    description = request.form.get('description') or ''
    tags = request.form.get('tags') or ''
    duration = request.form.get('duration') or '0'
    quality = request.form.get('quality') or '720p'

    if not title:
        return jsonify({'error': 'عنوان الزامی است'}), 400

    video_file = request.files.get('video')
    thumb_file = request.files.get('thumbnail')

    if not video_file:
        return jsonify({'error': 'فایل ویدیو یافت نشد'}), 400

    video_url = upload_file(video_file, video_file.filename, 'videos')
    if not video_url:
        return jsonify({'error': 'آپلود ویدیو ناموفق بود'}), 500

    thumb_url = None
    if thumb_file:
        thumb_url = upload_file(thumb_file, thumb_file.filename, 'thumbnails')

    with db() as conn:
        video = conn.execute(
            """INSERT INTO videos (user_id, title, description, tags, video_url, thumbnail_url, duration, quality)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
            (uid, title[:200], description[:5000], tags[:500], video_url, thumb_url, float(duration), quality)
        ).fetchone()
        conn.commit()
        return jsonify({'success': True, 'video_id': video['id']})

# ─── VIDEOS ───

def video_list_query(conn, sql, params, uid=None):
    rows = conn.execute(sql, params).fetchall()
    result = []
    for r in rows:
        v = dict(r)
        if isinstance(v.get('created_at'), datetime):
            v['created'] = int(v['created_at'].timestamp())
        if uid:
            like = conn.execute(
                "SELECT value FROM likes WHERE user_id=%s AND video_id=%s", (uid, v['id'])
            ).fetchone()
            v['user_liked'] = like['value'] if like else 0
        result.append(v)
    return result

@app.route('/api/videos', methods=['GET'])
def get_videos():
    sort = request.args.get('sort', 'newest')
    uid = session.get('user_id')
    order = 'v.created_at DESC' if sort == 'newest' else 'v.views DESC'
    with db() as conn:
        videos = video_list_query(conn, f"""
            SELECT v.*, u.display_name as author_name, u.avatar as author_avatar, u.verified as author_verified
            FROM videos v JOIN users u ON v.user_id=u.id
            WHERE v.is_short=FALSE ORDER BY {order} LIMIT 30
        """, (), uid)
        return jsonify({'videos': videos})

@app.route('/api/videos/<int:vid>', methods=['GET'])
def get_video(vid):
    uid = session.get('user_id')
    with db() as conn:
        r = conn.execute("""
            SELECT v.*, u.display_name as author_name, u.avatar as author_avatar,
                   u.verified as author_verified,
                   (SELECT COUNT(*) FROM subscriptions WHERE channel_id=v.user_id) as channel_subs
            FROM videos v JOIN users u ON v.user_id=u.id WHERE v.id=%s
        """, (vid,)).fetchone()
        if not r:
            return jsonify({'error': 'ویدیو یافت نشد'}), 404
        v = dict(r)
        if isinstance(v.get('created_at'), datetime):
            v['created'] = int(v['created_at'].timestamp())
        if uid:
            like = conn.execute("SELECT value FROM likes WHERE user_id=%s AND video_id=%s", (uid, vid)).fetchone()
            v['user_liked'] = like['value'] if like else 0
            sub = conn.execute("SELECT id FROM subscriptions WHERE user_id=%s AND channel_id=%s", (uid, v['user_id'])).fetchone()
            v['user_subscribed'] = bool(sub)
        else:
            v['user_liked'] = 0
            v['user_subscribed'] = False
        return jsonify(v)

@app.route('/api/shorts', methods=['GET'])
def get_shorts():
    uid = session.get('user_id')
    with db() as conn:
        videos = video_list_query(conn, """
            SELECT v.*, u.display_name as author_name, u.avatar as author_avatar, u.verified as author_verified
            FROM videos v JOIN users u ON v.user_id=u.id
            WHERE v.is_short=TRUE ORDER BY v.created_at DESC LIMIT 20
        """, (), uid)
        return jsonify({'videos': videos})

@app.route('/api/trending', methods=['GET'])
def trending():
    uid = session.get('user_id')
    with db() as conn:
        videos = video_list_query(conn, """
            SELECT v.*, u.display_name as author_name, u.avatar as author_avatar, u.verified as author_verified
            FROM videos v JOIN users u ON v.user_id=u.id
            ORDER BY (v.views * 2 + v.likes_count) DESC LIMIT 30
        """, (), uid)
        return jsonify({'videos': videos})

@app.route('/api/search', methods=['GET'])
def search():
    q = request.args.get('q', '').strip()
    uid = session.get('user_id')
    if not q:
        return jsonify({'videos': []})
    with db() as conn:
        videos = video_list_query(conn, """
            SELECT v.*, u.display_name as author_name, u.avatar as author_avatar, u.verified as author_verified
            FROM videos v JOIN users u ON v.user_id=u.id
            WHERE v.title ILIKE %s OR v.description ILIKE %s OR v.tags ILIKE %s
            ORDER BY v.views DESC LIMIT 30
        """, (f'%{q}%', f'%{q}%', f'%{q}%'), uid)
        return jsonify({'videos': videos})

@app.route('/api/view', methods=['POST'])
def add_view():
    data = request.json
    vid = data.get('video_id')
    if vid:
        with db() as conn:
            conn.execute("UPDATE videos SET views=views+1 WHERE id=%s", (vid,))
            uid = session.get('user_id')
            if uid:
                conn.execute(
                    "INSERT INTO history (user_id, video_id) VALUES (%s,%s) ON CONFLICT (user_id,video_id) DO UPDATE SET watched_at=NOW()",
                    (uid, vid)
                )
            conn.commit()
    return jsonify({'success': True})

@app.route('/api/like', methods=['POST'])
def like_video():
    uid = session.get('user_id')
    if not uid:
        return jsonify({'error': 'لطفاً وارد شوید'}), 401
    data = request.json
    vid = data.get('video_id')
    value = data.get('value', 1)  # 1=like, -1=dislike, 0=remove

    with db() as conn:
        existing = conn.execute("SELECT value FROM likes WHERE user_id=%s AND video_id=%s", (uid, vid)).fetchone()
        if existing:
            if value == 0 or existing['value'] == value:
                conn.execute("DELETE FROM likes WHERE user_id=%s AND video_id=%s", (uid, vid))
                # update counts
                if existing['value'] == 1:
                    conn.execute("UPDATE videos SET likes_count=GREATEST(0,likes_count-1) WHERE id=%s", (vid,))
                else:
                    conn.execute("UPDATE videos SET dislikes_count=GREATEST(0,dislikes_count-1) WHERE id=%s", (vid,))
                user_liked = 0
            else:
                conn.execute("UPDATE likes SET value=%s WHERE user_id=%s AND video_id=%s", (value, uid, vid))
                if value == 1:
                    conn.execute("UPDATE videos SET likes_count=likes_count+1, dislikes_count=GREATEST(0,dislikes_count-1) WHERE id=%s", (vid,))
                else:
                    conn.execute("UPDATE videos SET dislikes_count=dislikes_count+1, likes_count=GREATEST(0,likes_count-1) WHERE id=%s", (vid,))
                user_liked = value
        else:
            if value != 0:
                conn.execute("INSERT INTO likes (user_id, video_id, value) VALUES (%s,%s,%s)", (uid, vid, value))
                if value == 1:
                    conn.execute("UPDATE videos SET likes_count=likes_count+1 WHERE id=%s", (vid,))
                else:
                    conn.execute("UPDATE videos SET dislikes_count=dislikes_count+1 WHERE id=%s", (vid,))
                user_liked = value
            else:
                user_liked = 0
        conn.commit()
        v = conn.execute("SELECT likes_count, dislikes_count FROM videos WHERE id=%s", (vid,)).fetchone()
        return jsonify({'success': True, 'user_liked': user_liked, 'likes': v['likes_count'], 'dislikes': v['dislikes_count']})

# ─── COMMENTS ───

@app.route('/api/comments/<int:vid>', methods=['GET'])
def get_comments(vid):
    uid = session.get('user_id')
    with db() as conn:
        rows = conn.execute("""
            SELECT c.*, u.display_name as author_name, u.avatar as author_avatar
            FROM comments c JOIN users u ON c.user_id=u.id
            WHERE c.video_id=%s AND c.parent_id IS NULL
            ORDER BY c.pinned DESC, c.likes_count DESC, c.created_at DESC LIMIT 100
        """, (vid,)).fetchall()
        comments = []
        for r in rows:
            c = dict(r)
            if isinstance(c.get('created_at'), datetime):
                c['created'] = int(c['created_at'].timestamp())
            if uid:
                cl = conn.execute("SELECT id FROM comment_likes WHERE user_id=%s AND comment_id=%s", (uid, c['id'])).fetchone()
                c['user_liked'] = bool(cl)
            replies = conn.execute("""
                SELECT c2.*, u2.display_name as author_name, u2.avatar as author_avatar
                FROM comments c2 JOIN users u2 ON c2.user_id=u2.id
                WHERE c2.parent_id=%s ORDER BY c2.created_at ASC LIMIT 20
            """, (c['id'],)).fetchall()
            c['replies'] = []
            for rr in replies:
                rep = dict(rr)
                if isinstance(rep.get('created_at'), datetime):
                    rep['created'] = int(rep['created_at'].timestamp())
                c['replies'].append(rep)
            comments.append(c)
        return jsonify({'comments': comments})

@app.route('/api/comment', methods=['POST'])
def add_comment():
    uid = session.get('user_id')
    if not uid:
        return jsonify({'error': 'لطفاً وارد شوید'}), 401
    data = request.json
    vid = data.get('video_id')
    text = (data.get('text') or '').strip()
    if not text:
        return jsonify({'error': 'متن خالی'}), 400
    with db() as conn:
        c = conn.execute(
            "INSERT INTO comments (user_id, video_id, text) VALUES (%s,%s,%s) RETURNING *",
            (uid, vid, text[:1000])
        ).fetchone()
        conn.execute("UPDATE videos SET comments_count=comments_count+1 WHERE id=%s", (vid,))
        # notify video owner
        video = conn.execute("SELECT user_id FROM videos WHERE id=%s", (vid,)).fetchone()
        if video and video['user_id'] != uid:
            conn.execute(
                "INSERT INTO notifications (user_id, from_user_id, type, video_id) VALUES (%s,%s,'comment',%s)",
                (video['user_id'], uid, vid)
            )
        conn.commit()
        return jsonify({'success': True, 'comment': dict(c)})

@app.route('/api/comment_reply', methods=['POST'])
def reply_comment():
    uid = session.get('user_id')
    if not uid:
        return jsonify({'error': 'لطفاً وارد شوید'}), 401
    data = request.json
    vid = data.get('video_id')
    text = (data.get('text') or '').strip()
    parent_id = data.get('parent_id')
    if not text:
        return jsonify({'error': 'متن خالی'}), 400
    with db() as conn:
        c = conn.execute(
            "INSERT INTO comments (user_id, video_id, text, parent_id) VALUES (%s,%s,%s,%s) RETURNING *",
            (uid, vid, text[:1000], parent_id)
        ).fetchone()
        conn.commit()
        return jsonify({'success': True, 'comment': dict(c)})

@app.route('/api/comment_like', methods=['POST'])
def like_comment():
    uid = session.get('user_id')
    if not uid:
        return jsonify({'error': 'لطفاً وارد شوید'}), 401
    cid = request.json.get('comment_id')
    with db() as conn:
        existing = conn.execute("SELECT id FROM comment_likes WHERE user_id=%s AND comment_id=%s", (uid, cid)).fetchone()
        if existing:
            conn.execute("DELETE FROM comment_likes WHERE user_id=%s AND comment_id=%s", (uid, cid))
            conn.execute("UPDATE comments SET likes_count=GREATEST(0,likes_count-1) WHERE id=%s", (cid,))
            liked = False
        else:
            conn.execute("INSERT INTO comment_likes (user_id, comment_id) VALUES (%s,%s)", (uid, cid))
            conn.execute("UPDATE comments SET likes_count=likes_count+1 WHERE id=%s", (cid,))
            liked = True
        conn.commit()
        return jsonify({'success': True, 'liked': liked})

@app.route('/api/delete_comment', methods=['POST'])
def delete_comment():
    uid = session.get('user_id')
    if not uid:
        return jsonify({'error': 'لطفاً وارد شوید'}), 401
    cid = request.json.get('comment_id')
    with db() as conn:
        conn.execute("DELETE FROM comments WHERE id=%s AND user_id=%s", (cid, uid))
        conn.commit()
        return jsonify({'success': True})

@app.route('/api/pin_comment', methods=['POST'])
def pin_comment():
    uid = session.get('user_id')
    if not uid:
        return jsonify({'error': 'لطفاً وارد شوید'}), 401
    cid = request.json.get('comment_id')
    with db() as conn:
        c = conn.execute("SELECT * FROM comments WHERE id=%s", (cid,)).fetchone()
        if not c:
            return jsonify({'error': 'یافت نشد'}), 404
        video = conn.execute("SELECT user_id FROM videos WHERE id=%s", (c['video_id'],)).fetchone()
        if not video or video['user_id'] != uid:
            return jsonify({'error': 'دسترسی ندارید'}), 403
        conn.execute("UPDATE comments SET pinned=NOT pinned WHERE id=%s", (cid,))
        conn.commit()
        return jsonify({'success': True})

# ─── SUBSCRIPTIONS ───

@app.route('/api/subscribe', methods=['POST'])
def subscribe():
    uid = session.get('user_id')
    if not uid:
        return jsonify({'error': 'لطفاً وارد شوید'}), 401
    channel_id = request.json.get('channel_id')
    with db() as conn:
        existing = conn.execute("SELECT id FROM subscriptions WHERE user_id=%s AND channel_id=%s", (uid, channel_id)).fetchone()
        if existing:
            conn.execute("DELETE FROM subscriptions WHERE user_id=%s AND channel_id=%s", (uid, channel_id))
            subscribed = False
        else:
            conn.execute("INSERT INTO subscriptions (user_id, channel_id) VALUES (%s,%s)", (uid, channel_id))
            conn.execute(
                "INSERT INTO notifications (user_id, from_user_id, type) VALUES (%s,%s,'subscribe')",
                (channel_id, uid)
            )
            subscribed = True
        conn.commit()
        return jsonify({'success': True, 'subscribed': subscribed})

@app.route('/api/subscriptions', methods=['GET'])
def subscriptions():
    uid = session.get('user_id')
    if not uid:
        return jsonify({'videos': []})
    with db() as conn:
        videos = video_list_query(conn, """
            SELECT v.*, u.display_name as author_name, u.avatar as author_avatar, u.verified as author_verified
            FROM videos v JOIN users u ON v.user_id=u.id
            WHERE v.user_id IN (SELECT channel_id FROM subscriptions WHERE user_id=%s)
            ORDER BY v.created_at DESC LIMIT 30
        """, (uid,), uid)
        return jsonify({'videos': videos})

@app.route('/api/feed', methods=['GET'])
def feed():
    uid = session.get('user_id')
    with db() as conn:
        if uid:
            videos = video_list_query(conn, """
                SELECT v.*, u.display_name as author_name, u.avatar as author_avatar, u.verified as author_verified
                FROM videos v JOIN users u ON v.user_id=u.id
                WHERE v.user_id IN (SELECT channel_id FROM subscriptions WHERE user_id=%s)
                   OR v.views > 100
                ORDER BY v.created_at DESC LIMIT 20
            """, (uid,), uid)
        else:
            videos = video_list_query(conn, """
                SELECT v.*, u.display_name as author_name, u.avatar as author_avatar, u.verified as author_verified
                FROM videos v JOIN users u ON v.user_id=u.id
                ORDER BY v.views DESC LIMIT 20
            """, (), None)
        return jsonify({'videos': videos})

# ─── USER / CHANNEL ───

@app.route('/api/user/<int:uid_param>', methods=['GET'])
def get_user(uid_param):
    uid = session.get('user_id')
    with db() as conn:
        user = conn.execute("SELECT id,username,display_name,avatar,bio,verified,created_at FROM users WHERE id=%s", (uid_param,)).fetchone()
        if not user:
            return jsonify({'error': 'کاربر یافت نشد'}), 404
        u = dict(user)
        u['subs'] = conn.execute("SELECT COUNT(*) as c FROM subscriptions WHERE channel_id=%s", (uid_param,)).fetchone()['c']
        if uid:
            s = conn.execute("SELECT id FROM subscriptions WHERE user_id=%s AND channel_id=%s", (uid, uid_param)).fetchone()
            u['user_subscribed'] = bool(s)
        return jsonify(u)

@app.route('/api/user/<int:uid_param>/videos', methods=['GET'])
def user_videos(uid_param):
    uid = session.get('user_id')
    with db() as conn:
        videos = video_list_query(conn, """
            SELECT v.*, u.display_name as author_name, u.avatar as author_avatar, u.verified as author_verified
            FROM videos v JOIN users u ON v.user_id=u.id
            WHERE v.user_id=%s ORDER BY v.created_at DESC LIMIT 50
        """, (uid_param,), uid)
        return jsonify({'videos': videos})

@app.route('/api/update_profile', methods=['POST'])
def update_profile():
    uid = session.get('user_id')
    if not uid:
        return jsonify({'error': 'لطفاً وارد شوید'}), 401

    display_name = request.form.get('display_name') or ''
    bio = request.form.get('bio') or ''
    avatar_file = request.files.get('avatar')

    with db() as conn:
        avatar_url = None
        if avatar_file:
            avatar_url = upload_file(avatar_file, avatar_file.filename, 'avatars')

        if avatar_url:
            conn.execute(
                "UPDATE users SET display_name=%s, bio=%s, avatar=%s WHERE id=%s",
                (display_name[:100], bio[:300], avatar_url, uid)
            )
        else:
            conn.execute(
                "UPDATE users SET display_name=%s, bio=%s WHERE id=%s",
                (display_name[:100], bio[:300], uid)
            )
        conn.commit()
        user = conn.execute("SELECT id,username,display_name,avatar,bio,verified FROM users WHERE id=%s", (uid,)).fetchone()
        return jsonify({'success': True, 'user': dict(user)})

@app.route('/api/change_password', methods=['POST'])
def change_password():
    uid = session.get('user_id')
    if not uid:
        return jsonify({'error': 'لطفاً وارد شوید'}), 401
    data = request.json
    old_pw = data.get('old_password') or ''
    new_pw = data.get('new_password') or ''
    if len(new_pw) < 6:
        return jsonify({'error': 'رمز جدید حداقل ۶ کاراکتر'}), 400
    with db() as conn:
        user = conn.execute("SELECT password FROM users WHERE id=%s", (uid,)).fetchone()
        if user['password'] != hash_pw(old_pw):
            return jsonify({'error': 'رمز فعلی اشتباه است'}), 401
        conn.execute("UPDATE users SET password=%s WHERE id=%s", (hash_pw(new_pw), uid))
        conn.commit()
        return jsonify({'success': True})

# ─── HISTORY / WATCHLATER ───

@app.route('/api/history', methods=['GET'])
def history():
    uid = session.get('user_id')
    if not uid:
        return jsonify({'videos': []})
    with db() as conn:
        videos = video_list_query(conn, """
            SELECT v.*, u.display_name as author_name, u.avatar as author_avatar, u.verified as author_verified
            FROM videos v JOIN users u ON v.user_id=u.id
            JOIN history h ON h.video_id=v.id
            WHERE h.user_id=%s ORDER BY h.watched_at DESC LIMIT 50
        """, (uid,), uid)
        return jsonify({'videos': videos})

@app.route('/api/watchlater', methods=['POST'])
def watchlater():
    uid = session.get('user_id')
    if not uid:
        return jsonify({'error': 'لطفاً وارد شوید'}), 401
    vid = request.json.get('video_id')
    with db() as conn:
        existing = conn.execute("SELECT id FROM watchlater WHERE user_id=%s AND video_id=%s", (uid, vid)).fetchone()
        if existing:
            conn.execute("DELETE FROM watchlater WHERE user_id=%s AND video_id=%s", (uid, vid))
            added = False
        else:
            conn.execute("INSERT INTO watchlater (user_id, video_id) VALUES (%s,%s)", (uid, vid))
            added = True
        conn.commit()
        return jsonify({'success': True, 'added': added})

@app.route('/api/watchlater', methods=['GET'])
def get_watchlater():
    uid = session.get('user_id')
    if not uid:
        return jsonify({'videos': []})
    with db() as conn:
        videos = video_list_query(conn, """
            SELECT v.*, u.display_name as author_name, u.avatar as author_avatar, u.verified as author_verified
            FROM videos v JOIN users u ON v.user_id=u.id
            JOIN watchlater wl ON wl.video_id=v.id
            WHERE wl.user_id=%s ORDER BY wl.created_at DESC LIMIT 50
        """, (uid,), uid)
        return jsonify({'videos': videos})

# ─── NOTIFICATIONS ───

@app.route('/api/notifications', methods=['GET'])
def notifications():
    uid = session.get('user_id')
    if not uid:
        return jsonify({'notifications': []})
    with db() as conn:
        rows = conn.execute("""
            SELECT n.*, u.display_name as from_name, u.avatar as from_avatar
            FROM notifications n JOIN users u ON n.from_user_id=u.id
            WHERE n.user_id=%s ORDER BY n.created_at DESC LIMIT 50
        """, (uid,)).fetchall()
        ns = []
        for r in rows:
            n = dict(r)
            if isinstance(n.get('created_at'), datetime):
                n['created'] = int(n['created_at'].timestamp())
            ns.append(n)
        conn.execute("UPDATE notifications SET read=TRUE WHERE user_id=%s", (uid,))
        conn.commit()
        return jsonify({'notifications': ns})

# ─── DELETE VIDEO ───

@app.route('/api/delete_video', methods=['POST'])
def delete_video():
    uid = session.get('user_id')
    if not uid:
        return jsonify({'error': 'لطفاً وارد شوید'}), 401
    vid = request.json.get('video_id')
    with db() as conn:
        conn.execute("DELETE FROM videos WHERE id=%s AND user_id=%s", (vid, uid))
        conn.commit()
        return jsonify({'success': True})

# ─── MISC ───

@app.route('/api/share', methods=['POST'])
def share():
    vid = request.json.get('video_id')
    if vid:
        with db() as conn:
            conn.execute("UPDATE videos SET shares=shares+1 WHERE id=%s", (vid,))
            conn.commit()
    return jsonify({'success': True})

@app.route('/api/report', methods=['POST'])
def report():
    uid = session.get('user_id')
    data = request.json
    with db() as conn:
        conn.execute(
            "INSERT INTO reports (user_id, type, target_id, reason) VALUES (%s,%s,%s,%s)",
            (uid, data.get('type'), data.get('target_id'), data.get('reason'))
        )
        conn.commit()
    return jsonify({'success': True})

@app.route('/api/algorithm', methods=['POST'])
def algorithm():
    return jsonify({'success': True})

@app.route('/api/analytics', methods=['GET'])
def analytics():
    uid = session.get('user_id')
    if not uid:
        return jsonify({'error': 'لطفاً وارد شوید'}), 401
    with db() as conn:
        vc = conn.execute("SELECT COUNT(*) as c FROM videos WHERE user_id=%s", (uid,)).fetchone()['c']
        tv = conn.execute("SELECT COALESCE(SUM(views),0) as s FROM videos WHERE user_id=%s", (uid,)).fetchone()['s']
        subs = conn.execute("SELECT COUNT(*) as c FROM subscriptions WHERE channel_id=%s", (uid,)).fetchone()['c']
        tl = conn.execute("SELECT COALESCE(SUM(likes_count),0) as s FROM videos WHERE user_id=%s", (uid,)).fetchone()['s']
        return jsonify({'video_count': vc, 'total_views': tv, 'subscribers': subs, 'total_likes': tl})

@app.route('/health')
def health():
    return jsonify({'status': 'ok'})

@app.route('/')
def home():
    return jsonify({'name': 'YouKo API', 'version': '1.0', 'status': 'running'})

if __name__ == '__main__':
    port = int(os.getenv('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False)

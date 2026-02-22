import streamlit as st
import pandas as pd
from datetime import datetime, timedelta, date, time
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Float, ForeignKey, Time, Boolean, text
from sqlalchemy.orm import declarative_base, sessionmaker

# ==========================================
# 0. הגדרות תצוגה ו-RTL
# ==========================================
st.set_page_config(page_title="מערכת שיבוץ - מילואים", layout="centered")

st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Assistant:wght@400;700&display=swap');

    html, body, [data-testid="stAppViewContainer"], .main {
        direction: rtl;
        text-align: right;
        font-family: 'Assistant', sans-serif;
    }

    h1, h2, h3, h4, h5, h6, p, label, .stMarkdown, .stSelectbox, .stTextInput, .stNumberInput, .stTimeInput, .stDateInput {
        direction: rtl;
        text-align: right;
    }

    /* רוחב דינאמי: מבטל גלילה אך נשאר קומפקטי ולא מתפזר על מסך רחב מדי */
    .block-container {
        max-width: 95% !important;
        padding-left: 1rem !important;
        padding-right: 1rem !important;
    }

    [data-testid="stDataFrame"], [data-testid="stDataEditor"] {
        direction: rtl;
        text-align: right;
    }
    
    div[data-testid="stCellInner"], 
    div[data-testid="stTableColumnHeaderInner"] {
        justify-content: flex-end !important; 
        text-align: right !important;
    }
    
    table {
        direction: rtl !important;
        width: 100% !important;
    }
    th, td {
        text-align: right !important;
    }

    .stButton>button { width: 100%; font-weight: bold; border-radius: 8px; }
    
    /* עיצוב כפתורים: כפתור Primary ירוק-אמרלד (חי וידידותי) */
    button[kind="primary"] {
        background-color: #059669 !important; 
        border-color: #059669 !important;
        color: white !important;
    }
    button[kind="primary"]:hover {
        background-color: #047857 !important;
        border-color: #047857 !important;
    }

    /* כפתור נקה לוח - צבע ניטרלי עדין */
    button[kind="secondary"] {
        border-color: #d1d5db !important;
    }

    /* כפתורים מסוכנים (מחיקות איפוס) נשארים באדום */
    .danger-zone button {
        background-color: #dc2626 !important;
        border-color: #dc2626 !important;
        color: white !important;
    }
    .danger-zone button:hover {
        background-color: #b91c1c !important;
        border-color: #b91c1c !important;
    }

    .post-header { 
        text-align: center; 
        padding: 12px; 
        background-color: #1e40af; 
        color: white; 
        border-radius: 8px 8px 0 0; 
        margin-bottom: 0px; 
        font-weight: bold; 
    }

    .alert-box {
        background-color: #fef2f2;
        border: 1px solid #ef4444;
        color: #b91c1c;
        padding: 15px;
        border-radius: 8px;
        font-size: 0.95rem;
        margin-top: 20px;
        direction: rtl;
        text-align: right;
    }

    .danger-zone {
        border: 1px solid #dc2626;
        padding: 10px;
        border-radius: 8px;
        background-color: #fff5f5;
        margin-top: 20px;
    }
    </style>
""", unsafe_allow_html=True)

# ==========================================
# 1. סכמת נתונים (Database)
# ==========================================
Base = declarative_base()

class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    total_hours = Column(Float, default=0.0)
    is_commander = Column(Boolean, default=False) 

class Post(Base):
    __tablename__ = 'posts'
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    shift_length_minutes = Column(Integer, default=120)
    required_guards = Column(Integer, default=1)
    active_from = Column(Time, default=time(0, 0))
    active_to = Column(Time, default=time(23, 59))
    boost_from = Column(Time, nullable=True)
    boost_to = Column(Time, nullable=True)
    boost_guards = Column(Integer, default=0)
    requires_commander = Column(Boolean, default=False) 

class Shift(Base):
    __tablename__ = 'shifts'
    id = Column(Integer, primary_key=True)
    post_id = Column(Integer, ForeignKey('posts.id'))
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=False)
    assigned_user_ids = Column(String, default="") 
    required_count = Column(Integer, default=1)

class Constraint(Base):
    __tablename__ = 'constraints'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'))
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=False)
    reason = Column(String)

class PairingRule(Base):
    __tablename__ = 'pairing_rules'
    id = Column(Integer, primary_key=True)
    user1_id = Column(Integer, ForeignKey('users.id'))
    user2_id = Column(Integer, ForeignKey('users.id'))
    rule_type = Column(String) 

class PostConstraint(Base):
    __tablename__ = 'post_constraints'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'))
    post_id = Column(Integer, ForeignKey('posts.id'))

class SystemSetting(Base):
    __tablename__ = 'system_settings'
    key = Column(String, primary_key=True)
    value = Column(String)

engine = create_engine('sqlite:///shifts_v8.db', connect_args={'check_same_thread': False})
Base.metadata.create_all(engine)

# QA Fix: שדרוג מסד הנתונים שקוף למשתמש למקרה שזה מסד ישן
with engine.connect() as conn:
    try: conn.execute(text("ALTER TABLE users ADD COLUMN is_commander BOOLEAN DEFAULT 0"))
    except: pass
    try: conn.execute(text("ALTER TABLE posts ADD COLUMN requires_commander BOOLEAN DEFAULT 0"))
    except: pass
    conn.commit()

SessionLocal = sessionmaker(bind=engine)
MIN_REST_HOURS = 6

# ==========================================
# 2. פונקציות עזר ואלגוריתם שיבוץ משופר (AI)
# ==========================================
def is_time_in_range(start, end, current):
    if start <= end:
        return start <= current <= end
    return current >= start or current <= end

def is_black_shift(start_dt, end_dt):
    # מגדיר "משמרת שחורה" כמשמרת שאמצע הזמן שלה נופל בין 00:00 ל-06:00
    midpoint = start_dt + (end_dt - start_dt) / 2
    return 0 <= midpoint.hour < 6

def get_shift_warnings(db_session, target_date, days_to_add=1):
    start_dt = datetime.combine(target_date, time(0,0))
    end_dt = start_dt + timedelta(days=days_to_add) 
    shifts = db_session.query(Shift).filter(Shift.start_time >= start_dt, Shift.start_time < end_dt).all()
    warnings = {}
    
    # QA Optimization: Caching db calls for O(1) lookups
    posts_cache = {p.id: p for p in db_session.query(Post).all()}
    users_cache = {u.id: u for u in db_session.query(User).all()}

    for s in shifts:
        assigned_ids = [int(x) for x in (s.assigned_user_ids or "").split(",") if x]
        post_obj = posts_cache.get(s.post_id)
        
        if len(assigned_ids) < s.required_count:
            warnings[s.id] = f"בעמדת {post_obj.name if post_obj else s.post_id}: חסר שומר ({len(assigned_ids)}/{s.required_count})"
        
        if post_obj and post_obj.requires_commander and assigned_ids:
            if not any(users_cache[uid].is_commander for uid in assigned_ids if uid in users_cache):
                warnings[s.id] = f"בעמדת {post_obj.name}: חובה לשבץ לפחות מפקד אחד (⭐)"

        for uid in assigned_ids:
            u_obj = users_cache.get(uid)
            u_name = u_obj.name if u_obj else "שומר"
            
            if db_session.query(PostConstraint).filter_by(user_id=uid, post_id=s.post_id).first():
                warnings[s.id] = f"אילוץ לשומר {u_name}: אינו מורשה לשמור בעמדה זו"
            
            c = db_session.query(Constraint).filter(Constraint.user_id == uid, Constraint.start_time < s.end_time, Constraint.end_time > s.start_time).first()
            if c: warnings[s.id] = f"אילוץ לשומר {u_name}: {c.reason}"
            
            prev_s = db_session.query(Shift).filter(
                Shift.end_time <= s.start_time,
                Shift.assigned_user_ids.like(f"%{uid}%")
            ).order_by(Shift.end_time.desc()).first()
            
            if prev_s:
                rest = (s.start_time - prev_s.end_time).total_seconds() / 3600
                if rest < MIN_REST_HOURS:
                    prev_post_name = posts_cache[prev_s.post_id].name if prev_s.post_id in posts_cache else "לא ידוע"
                    warnings[s.id] = f"חריגת מנוחה ל{u_name}: שמר קודם ב{prev_post_name} ({prev_s.start_time.strftime('%H:%M')}-{prev_s.end_time.strftime('%H:%M')}). נח {rest:.1f} ש' (אילוץ)."
    return warnings

def auto_assign_shifts(db_session, target_date, days_to_add=1):
    start_dt = datetime.combine(target_date, time(0,0))
    end_dt = start_dt + timedelta(days=days_to_add) 
    unassigned_shifts = db_session.query(Shift).filter(Shift.start_time >= start_dt, Shift.start_time < end_dt).order_by(Shift.start_time).all()
    users = db_session.query(User).all()
    posts = {p.id: p for p in db_session.query(Post).all()}
    
    all_shifts = db_session.query(Shift).filter(Shift.assigned_user_ids != "").all()
    
    # ניתוח אנליטי לכל חייל כולל ספירת משמרות שחורות
    user_stats = {str(u.id): {"total": 0.0, "daily": 0.0, "black_shifts": 0} for u in users}
    
    for s in all_shifts:
        duration = (s.end_time - s.start_time).total_seconds() / 3600.0
        is_today = start_dt <= s.start_time < end_dt
        is_black = is_black_shift(s.start_time, s.end_time)
        
        for uid in (s.assigned_user_ids or "").split(","):
            if uid in user_stats:
                user_stats[uid]["total"] += duration
                if is_today: user_stats[uid]["daily"] += duration
                if is_black: user_stats[uid]["black_shifts"] += 1
    
    rules_dict = {
        (str(r.user1_id), str(r.user2_id)): r.rule_type
        for r in db_session.query(PairingRule).all()
    }
    rules_dict.update({(k[1], k[0]): v for k, v in rules_dict.items()}) # דו-כיווני
        
    blocked_posts = {(pc.user_id, pc.post_id) for pc in db_session.query(PostConstraint).all()}
    
    for shift in unassigned_shifts:
        assigned_list = [x for x in (shift.assigned_user_ids or "").split(",") if x]
        needed = shift.required_count - len(assigned_list)
        post_obj = posts.get(shift.post_id)
        req_cmd = post_obj.requires_commander if post_obj else False
        current_is_black = is_black_shift(shift.start_time, shift.end_time)
        
        for _ in range(needed):
            db_session.flush()
            candidates = []
            has_cmd = any(next((u.is_commander for u in users if str(u.id) == a), False) for a in assigned_list)
            
            for user in users:
                uid_str = str(user.id)
                if uid_str in assigned_list: continue
                if (user.id, shift.post_id) in blocked_posts: continue
                
                # חפיפות ואילוצים
                u_s = [s for s in db_session.query(Shift).filter(Shift.start_time >= start_dt - timedelta(hours=24)).all() if str(user.id) in (s.assigned_user_ids or "").split(",")]
                if any(max(shift.start_time, s.start_time) < min(shift.end_time, s.end_time) for s in u_s if s.id != shift.id): continue
                if db_session.query(Constraint).filter(Constraint.user_id == user.id, Constraint.start_time < shift.end_time, Constraint.end_time > shift.start_time).first(): continue

                is_anti_buddy = any(rules_dict.get((uid_str, a_uid)) == 'ANTI_BUDDY' for a_uid in assigned_list)
                if is_anti_buddy: continue
                
                buddy_score = sum(1 for a_uid in assigned_list if rules_dict.get((uid_str, a_uid)) == 'BUDDY')

                last_s = max([s for s in u_s if s.end_time <= shift.start_time], key=lambda x: x.end_time, default=None)
                rest = (shift.start_time - last_s.end_time).total_seconds() / 3600.0 if last_s else 999
                
                cmd_priority = 1 if (req_cmd and not has_cmd and user.is_commander) else 0
                
                candidates.append({
                    "user": user, 
                    "total": user_stats[uid_str]["total"], 
                    "daily": user_stats[uid_str]["daily"], 
                    "rest": rest, 
                    "buddy_score": buddy_score, 
                    "cmd_priority": cmd_priority,
                    "black_shifts": user_stats[uid_str]["black_shifts"]
                })
            
            if candidates:
                # מנוע הוגנות משופר: מנוחה > צורך במפקד > חמ"ד > הוגנות משמרות שחורות > שעות
                candidates.sort(key=lambda c: (
                    c["rest"] < MIN_REST_HOURS, 
                    -c["cmd_priority"], 
                    -c["buddy_score"], 
                    c["black_shifts"] if current_is_black else 0, # הוגנות נטל שחור
                    c["daily"], 
                    c["total"], 
                    -c["rest"]
                ))
                best = candidates[0]["user"]
                best_uid = str(best.id)
                
                assigned_list.append(best_uid)
                if best.is_commander: has_cmd = True 
                
                duration = (shift.end_time - shift.start_time).total_seconds() / 3600.0
                user_stats[best_uid]["total"] += duration
                user_stats[best_uid]["daily"] += duration
                if current_is_black: user_stats[best_uid]["black_shifts"] += 1
                best.total_hours += duration
                shift.assigned_user_ids = ",".join(assigned_list)
    db_session.commit()

# הפונקציה שמתריעה לשרת שאנחנו רוצים לשמור מה-UI
def flag_save():
    st.session_state.save_clicked = True

# ==========================================
# 3. טאב דשבורד
# ==========================================
def render_dashboard_tab(db_session):
    st.header("לוח שיבוצים מרכזי 🛡️")
    
    tools_container = st.container()
    with tools_container:
        col_date, col_auto, col_clear, col_save = st.columns([1.5, 1.2, 1, 1])
        with col_date:
            selected_date = st.date_input("תאריך התחלה:", date.today())
            view_mode = st.radio("תצוגת לוח:", ["24 שעות", "48 שעות"], horizontal=True, label_visibility="collapsed")
            days_to_show = 1 if view_mode == "24 שעות" else 2
        with col_auto:
            st.write("") 
            if st.button("🤖 שיבוץ אוטומטי חכם", type="primary", use_container_width=True):
                auto_assign_shifts(db_session, selected_date, days_to_show)
                st.success("השיבוץ הושלם!")
                st.rerun()
        with col_clear:
            st.write("") 
            if st.button("🧹 נקה לוח ידנית", use_container_width=True):
                s_clear = datetime.combine(selected_date, time(0,0))
                e_clear = s_clear + timedelta(days=days_to_show)
                shifts_to_clear = db_session.query(Shift).filter(Shift.start_time >= s_clear, Shift.start_time < e_clear).all()
                for s in shifts_to_clear:
                    s.assigned_user_ids = ""
                db_session.commit()
                st.success("הלוח נוקה!")
                st.rerun()
        with col_save:
            st.write("") 
            # התיקון של השמירה - מפעיל את on_click
            st.button("💾 שמור שינויים ידניים", type="primary", use_container_width=True, on_click=flag_save)
            
    st.divider()

    time_setting = db_session.query(SystemSetting).filter_by(key="time_display").first()
    time_format_full = True if not time_setting or time_setting.value == "full" else False

    users = db_session.query(User).all()
    posts = db_session.query(Post).all()
    id_to_name = {str(u.id): f"{u.name} ⭐" if u.is_commander else u.name for u in users}
    name_to_id = {f"{u.name} ⭐" if u.is_commander else u.name: str(u.id) for u in users}
    
    if not posts:
        st.info("נא להגדיר עמדות בטאב 'הגדרות'.")
        return

    start_view = datetime.combine(selected_date, time(0,0))
    end_view = start_view + timedelta(days=days_to_show) 
    
    warnings_dict = get_shift_warnings(db_session, selected_date, days_to_show)
    post_cols = st.columns(len(posts))
    
    for i, post in enumerate(posts):
        with post_cols[i]:
            st.markdown(f'<div class="post-header">{post.name} {"(👮‍♂️)" if post.requires_commander else ""}</div>', unsafe_allow_html=True)
            p_shifts = db_session.query(Shift).filter(Shift.post_id == post.id, Shift.start_time >= start_view, Shift.start_time < end_view).order_by(Shift.start_time).all()
            
            if not p_shifts:
                st.caption("אין משמרות בטווח הזמן הזה.")
                continue

            data = []
            max_g = max([s.required_count for s in p_shifts])
            
            for s in p_shifts:
                err_mark = "🛑 " if s.id in warnings_dict else ""
                assigned = (s.assigned_user_ids or "").split(",")
                
                s_f = s.start_time.strftime('%d/%m %H:%M') if days_to_show == 2 else s.start_time.strftime('%H:%M')
                e_f = s.end_time.strftime('%H:%M')
                t_str = f"{s_f} - {e_f}" if time_format_full else s_f
                
                row = {"ID": s.id, "זמן": f"{err_mark}{t_str}"}
                for j in range(max_g):
                    row[f"שומר {j+1}"] = id_to_name.get(assigned[j] if j < len(assigned) else "", "-- פנוי --")
                data.append(row)
            
            df = pd.DataFrame(data)
            df = df.iloc[:, ::-1] 
            config = {"ID": None, "זמן": st.column_config.TextColumn(disabled=True)}
            for j in range(max_g):
                config[f"שומר {j+1}"] = st.column_config.SelectboxColumn(options=["-- פנוי --"] + list(name_to_id.keys()))
            
            edited_df = st.data_editor(df.style.set_properties(**{'text-align': 'right'}), 
                                       column_config=config, hide_index=True, key=f"d_{post.id}_{selected_date}", use_container_width=True)
            
            # עריכה חכמה: רק מכין את הנתונים ב-Session, ממתין לפקודת Commit
            for _, r in edited_df.iterrows():
                s_obj = db_session.query(Shift).get(r["ID"])
                u_names = [r[f"שומר {j+1}"] for j in range(max_g) if f"שומר {j+1}" in r and r[f"שומר {j+1}"] != "-- פנוי --"]
                new_assigned = ",".join([name_to_id[n] for n in u_names if n in name_to_id])
                if s_obj.assigned_user_ids != new_assigned:
                    s_obj.assigned_user_ids = new_assigned

    if warnings_dict:
        st.markdown('<div class="alert-box"><strong>🚨 חריגות בלוח:</strong><br/>' + 
                    "<br/>".join([f"• {v}" for v in warnings_dict.values()]) + '</div>', unsafe_allow_html=True)

    # QA Fix Application: רק אם לחצו על הכפתור למעלה - נשמור את כל מה שנאסף בלולאה
    if st.session_state.get("save_clicked"):
        db_session.commit()
        st.session_state.save_clicked = False
        st.success("השינויים הידניים נשמרו בהצלחה!")

# ==========================================
# 3.5. טאב תצוגה לצילום מסך (View Only)
# ==========================================
def render_screenshot_tab(db_session):
    st.header("📸 תצוגה נקייה לצילום מסך")
    st.caption("הטבלאות חסומות לעריכה. מיושרות היטב לצילום מסך ושליחה בקבוצה.")
    
    col1, col2 = st.columns(2)
    selected_date = col1.date_input("תאריך התחלה:", date.today(), key="screenshot_date")
    view_mode = col2.radio("תצוגת לוח:", ["24 שעות", "48 שעות"], horizontal=True, key="screen_radio")
    days_to_show = 1 if view_mode == "24 שעות" else 2
    
    time_setting = db_session.query(SystemSetting).filter_by(key="time_display").first()
    time_format_full = True if not time_setting or time_setting.value == "full" else False
    
    users = db_session.query(User).all()
    posts = db_session.query(Post).all()
    id_to_name = {str(u.id): f"{u.name} ⭐" if u.is_commander else u.name for u in users}
    
    if not posts:
        st.info("אין עמדות במערכת.")
        return

    start_view = datetime.combine(selected_date, time(0,0))
    end_view = start_view + timedelta(days=days_to_show)
    
    post_cols = st.columns(len(posts))
    
    for i, post in enumerate(posts):
        with post_cols[i]:
            p_shifts = db_session.query(Shift).filter(Shift.post_id == post.id, Shift.start_time >= start_view, Shift.start_time < end_view).order_by(Shift.start_time).all()
            
            if not p_shifts:
                continue
            
            st.markdown(f'<div class="post-header" style="background-color: #0f766e;">{post.name}</div>', unsafe_allow_html=True)

            data = []
            max_g = max([s.required_count for s in p_shifts])
            
            for s in p_shifts:
                assigned = (s.assigned_user_ids or "").split(",")
                s_f = s.start_time.strftime('%d/%m %H:%M') if days_to_show == 2 else s.start_time.strftime('%H:%M')
                e_f = s.end_time.strftime('%H:%M')
                t_str = f"{s_f} - {e_f}" if time_format_full else s_f
                
                row = {"זמן": t_str}
                for j in range(max_g):
                    row[f"שומר {j+1}"] = id_to_name.get(assigned[j] if j < len(assigned) else "", "— פנוי —")
                data.append(row)
            
            if data:
                df = pd.DataFrame(data)
                st.table(df.style.set_properties(**{'text-align': 'right', 'background-color': '#ffffff'}))

# ==========================================
# 4. טאב כוח אדם
# ==========================================
def render_personnel_tab(db_session):
    st.header("ניהול כוח אדם ופילוח שעות 👥")
    
    col1, col2 = st.columns(2)
    with col1:
        with st.expander("➕ הוספת רשימת חיילים (Bulk Add)"):
            with st.form("bulk_add_form", clear_on_submit=True):
                bulk_text = st.text_area("הדבק שמות (מופרדים בפסיק או שורה חדשה):")
                is_cmds = st.checkbox("סמן את כולם כמפקדים (⭐)", False)
                if st.form_submit_button("הוסף את כולם"):
                    names = [n.strip() for n in bulk_text.replace(",", "\n").split("\n") if n.strip()]
                    for name in names:
                        if not db_session.query(User).filter_by(name=name).first():
                            db_session.add(User(name=name, is_commander=is_cmds))
                    db_session.commit()
                    st.rerun()

    with col2:
        with st.expander("🚫 הזנת אילוץ/חוסר זמינות"):
            all_users = db_session.query(User).all()
            if all_users:
                with st.form("add_constraint_form", clear_on_submit=True):
                    u_names = [f"{u.name} ⭐" if u.is_commander else u.name for u in all_users]
                    raw_names = [u.name for u in all_users]
                    sel_user_disp = st.selectbox("בחר חייל:", u_names)
                    idx = u_names.index(sel_user_disp)
                    sel_user = raw_names[idx]
                    
                    c_date = st.date_input("בתאריך:", date.today())
                    c_col1, c_col2 = st.columns(2)
                    t_from = c_col1.time_input("משעה:", time(8, 0))
                    t_to = c_col2.time_input("עד שעה:", time(12, 0))
                    c_reason = st.text_input("סיבה (אופציונלי):", "אילוץ אישי")
                    
                    if st.form_submit_button("שמור אילוץ"):
                        uid = db_session.query(User.id).filter_by(name=sel_user).scalar()
                        start_c = datetime.combine(c_date, t_from)
                        end_c = datetime.combine(c_date, t_to)
                        db_session.add(Constraint(user_id=uid, start_time=start_c, end_time=end_c, reason=c_reason))
                        db_session.commit()
                        st.toast(f"האילוץ נשמר בהצלחה.")
                        st.rerun()

    st.divider()
    users = db_session.query(User).all()
    posts = db_session.query(Post).all()
    shifts = db_session.query(Shift).filter(Shift.assigned_user_ids != "").all()
    
    summary = []
    for u in users:
        u_shifts = [s for s in shifts if str(u.id) in (s.assigned_user_ids or "").split(",")]
        total_real_hours = sum([(s.end_time - s.start_time).total_seconds()/3600 for s in u_shifts])
        black_shifts_count = sum(1 for s in u_shifts if is_black_shift(s.start_time, s.end_time))
        
        row = {"ID": u.id, "שם": u.name, "מפקד?": u.is_commander, "סה\"כ שעות": round(total_real_hours, 1), "משמרות 🌑": black_shifts_count}
        for p in posts:
            p_hrs = sum([(s.end_time - s.start_time).total_seconds()/3600 for s in u_shifts if s.post_id == p.id])
            row[f"שעות ב-{p.name}"] = round(p_hrs, 1)
        row["למחיקה"] = False
        summary.append(row)
    
    if summary:
        st.subheader("📊 מפת חלוקת נטל (אינטראקטיבי)")
        df_chart = pd.DataFrame(summary)
        st.bar_chart(df_chart.set_index("שם")["סה\"כ שעות"], color="#059669")
        
        st.subheader("📋 ניהול סד\"כ קבוע")
        df_sum = pd.DataFrame(summary)
        df_sum = df_sum.iloc[:, ::-1] 
        ed_p = st.data_editor(df_sum.style.set_properties(**{'text-align': 'right'}), hide_index=True, use_container_width=True)
        
        if st.button("💾 שמור שינויים בכוח אדם", type="primary"):
            for _, r in ed_p.iterrows():
                u_obj = db_session.query(User).get(r["ID"])
                if r["למחיקה"]: 
                    db_session.delete(u_obj)
                else: 
                    u_obj.name = r["שם"]
                    u_obj.is_commander = r["מפקד?"]
            db_session.commit()
            st.rerun()

    constraints = db_session.query(Constraint).all()
    if constraints:
        with st.expander("📋 אילוצים רשומים במערכת"):
            c_data = []
            for c in constraints:
                u_obj = db_session.query(User).get(c.user_id)
                u_n = f"{u_obj.name} ⭐" if u_obj.is_commander else u_obj.name
                c_data.append({"ID": c.id, "חייל": u_n, "התחלה": c.start_time.strftime('%d/%m %H:%M'), "סיום": c.end_time.strftime('%d/%m %H:%M'), "סיבה": c.reason, "מחק": False})
            df_c = pd.DataFrame(c_data)
            df_c = df_c.iloc[:, ::-1] 
            ed_c = st.data_editor(df_c.style.set_properties(**{'text-align': 'right'}), hide_index=True, use_container_width=True)
            if st.button("מחק אילוצים מסומנים"):
                for _, r in ed_c.iterrows():
                    if r["מחק"]: db_session.delete(db_session.query(Constraint).get(r["ID"]))
                db_session.commit()
                st.rerun()

    st.markdown('<div class="danger-zone">', unsafe_allow_html=True)
    st.subheader("⚠️ אזור סכנה")
    if st.button("🔄 איפוס מונה שעות לכולם"):
        for u in users: u.total_hours = 0
        db_session.commit()
        st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)

# ==========================================
# 5. טאב הגדרות
# ==========================================
def render_settings_tab(db_session):
    st.header("הגדרות מערכת ⚙️")
    
    tab_posts, tab_rules, tab_sys = st.tabs(["🏗️ ניהול ועריכת עמדות", "⚖️ זוגיות וחסימות", "🛠️ הגדרות כלליות"])
    
    users = db_session.query(User).all()
    posts = db_session.query(Post).all()
    
    with tab_posts:
        with st.expander("➕ הוספת עמדה חדשה", expanded=False):
            with st.form("add_post_form", clear_on_submit=True):
                name = st.text_input("שם העמדה")
                c1, c2 = st.columns(2)
                s_len = c1.number_input("אורך משמרת (דקות)", 120)
                base_g = c2.number_input("שומרים בבסיס", 1)
                
                req_cmd = st.checkbox("דורש מפקד בעמדה (לפחות אחד) ⭐", False)
                
                c3, c4 = st.columns(2)
                a_from = c3.time_input("פעילה מ-", time(0,0))
                a_to = c4.time_input("פעילה עד-", time(23,59))
                st.markdown("##### 🚀 שעות תגבור (אופציונלי)")
                c5, c6, c7 = st.columns(3)
                b_from = c5.time_input("תגבור מ-", time(0,0))
                b_to = c6.time_input("תגבור עד-", time(0,0))
                b_extra = c7.number_input("תוספת שומרים", 0)
                
                if st.form_submit_button("שמור עמדה"):
                    db_session.add(Post(name=name, shift_length_minutes=s_len, required_guards=base_g,
                                       active_from=a_from, active_to=a_to,
                                       boost_from=b_from, boost_to=b_to, boost_guards=b_extra,
                                       requires_commander=req_cmd))
                    db_session.commit()
                    st.rerun()

        if posts:
            st.subheader("עמדות קיימות (לעריכת חובת מפקד - מחק וצור מחדש)")
            p_list = [{"ID": p.id, "שם": p.name, "משך": p.shift_length_minutes, "שומרים": p.required_guards, "דורש מפקד": p.requires_commander, "למחיקה": False} for p in posts]
            df_p = pd.DataFrame(p_list)
            df_p = df_p.iloc[:, ::-1] 
            ed_p = st.data_editor(df_p.style.set_properties(**{'text-align': 'right'}), hide_index=True, use_container_width=True)
            if st.button("מחק עמדות מסומנות"):
                for _, r in ed_p.iterrows():
                    if r["למחיקה"]: 
                        db_session.query(Shift).filter_by(post_id=r["ID"]).delete()
                        db_session.delete(db_session.query(Post).get(r["ID"]))
                db_session.commit()
                st.rerun()

    with tab_rules:
        st.subheader("🤝 חמ\"ד / הפרדת כוחות")
        if len(users) >= 2:
            with st.expander("➕ הוספת כלל זוגיות חדש", expanded=False):
                with st.form("add_pairing_form", clear_on_submit=True):
                    u_names = [f"{u.name} ⭐" if u.is_commander else u.name for u in users]
                    raw_names = [u.name for u in users]
                    col_p1, col_p2, col_p3 = st.columns(3)
                    u1_disp = col_p1.selectbox("חייל א'", u_names, key="u1")
                    u2_disp = col_p2.selectbox("חייל ב'", u_names, key="u2")
                    u1_name = raw_names[u_names.index(u1_disp)]
                    u2_name = raw_names[u_names.index(u2_disp)]
                    
                    r_type = col_p3.selectbox("סוג קשר", ["חמ\"ד (תמיד יחד) 🟢", "הפרדת כוחות (לעולם לא יחד) 🔴"])
                    
                    if st.form_submit_button("שמור כלל זוגיות"):
                        if u1_name == u2_name:
                            st.error("לא ניתן לבחור את אותו חייל בשני הצדדים.")
                        else:
                            u1_id = next(u.id for u in users if u.name == u1_name)
                            u2_id = next(u.id for u in users if u.name == u2_name)
                            existing = db_session.query(PairingRule).filter(
                                ((PairingRule.user1_id == u1_id) & (PairingRule.user2_id == u2_id)) |
                                ((PairingRule.user1_id == u2_id) & (PairingRule.user2_id == u1_id))
                            ).first()
                            if existing:
                                st.warning("כבר קיים כלל עבור זוג זה. מחק אותו קודם.")
                            else:
                                db_type = 'BUDDY' if "חמ\"ד" in r_type else 'ANTI_BUDDY'
                                db_session.add(PairingRule(user1_id=u1_id, user2_id=u2_id, rule_type=db_type))
                                db_session.commit()
                                st.success("הכלל נשמר בהצלחה!")
                                st.rerun()

            rules = db_session.query(PairingRule).all()
            if rules:
                r_data = []
                for r in rules:
                    u1 = db_session.query(User).get(r.user1_id).name
                    u2 = db_session.query(User).get(r.user2_id).name
                    rt = "חמ\"ד 🟢" if r.rule_type == 'BUDDY' else "הפרדת כוחות 🔴"
                    r_data.append({"ID": r.id, "חייל א'": u1, "חייל ב'": u2, "סוג קשר": rt, "למחיקה": False})
                
                df_r = pd.DataFrame(r_data)
                df_r = df_r.iloc[:, ::-1] 
                ed_r = st.data_editor(df_r.style.set_properties(**{'text-align': 'right'}), hide_index=True, use_container_width=True)
                if st.button("מחק כללי זוגיות מסומנים"):
                    for _, row in ed_r.iterrows():
                        if row["למחיקה"]:
                            db_session.query(PairingRule).filter_by(id=row["ID"]).delete()
                    db_session.commit()
                    st.rerun()
        else:
            st.info("יש להוסיף לפחות 2 חיילים למערכת.")
        
        st.divider()
        st.subheader("🚫 אילוצי עמדות (מניעת שמירה)")
        if users and posts:
            with st.expander("➕ הוספת אילוץ עמדה לחייל", expanded=False):
                with st.form("add_post_constraint_form", clear_on_submit=True):
                    col_u, col_p = st.columns(2)
                    u_names = [f"{u.name} ⭐" if u.is_commander else u.name for u in users]
                    raw_names = [u.name for u in users]
                    u_disp = col_u.selectbox("בחר חייל:", u_names)
                    u_name = raw_names[u_names.index(u_disp)]
                    p_name = col_p.selectbox("בחר עמדה שחסומה לו:", [p.name for p in posts])
                    
                    if st.form_submit_button("שמור אילוץ עמדה"):
                        u_id = next(u.id for u in users if u.name == u_name)
                        p_id = next(p.id for p in posts if p.name == p_name)
                        if not db_session.query(PostConstraint).filter_by(user_id=u_id, post_id=p_id).first():
                            db_session.add(PostConstraint(user_id=u_id, post_id=p_id))
                            db_session.commit()
                            st.success("אילוץ העמדה נשמר!")
                            st.rerun()
                        else:
                            st.warning("האילוץ הזה כבר קיים במערכת.")
                            
            pcs = db_session.query(PostConstraint).all()
            if pcs:
                pc_data = []
                for pc in pcs:
                    u_obj = db_session.query(User).get(pc.user_id)
                    u_n = f"{u_obj.name} ⭐" if u_obj.is_commander else u_obj.name
                    p_n = db_session.query(Post).get(pc.post_id).name
                    pc_data.append({"ID": pc.id, "חייל": u_n, "עמדה חסומה": p_n, "למחיקה": False})
                
                df_pc = pd.DataFrame(pc_data)
                df_pc = df_pc.iloc[:, ::-1] 
                ed_pc = st.data_editor(df_pc.style.set_properties(**{'text-align': 'right'}), hide_index=True, use_container_width=True)
                if st.button("מחק אילוצי עמדה מסומנים"):
                    for _, row in ed_pc.iterrows():
                        if row["למחיקה"]:
                            db_session.query(PostConstraint).filter_by(id=row["ID"]).delete()
                    db_session.commit()
                    st.rerun()

    with tab_sys:
        st.subheader("תצוגת זמן בטבלאות")
        time_setting = db_session.query(SystemSetting).filter_by(key="time_display").first()
        curr_time_val = time_setting.value if time_setting else "full"
        
        new_time_val = st.radio("בחר תצוגה:", 
                           options=["full", "short"], 
                           format_func=lambda x: "טווח מלא (12/05 08:00 - 10:00)" if x == "full" else "שעת התחלה בלבד (12/05 08:00)",
                           index=0 if curr_time_val == "full" else 1)
                           
        if new_time_val != curr_time_val:
            if not time_setting:
                db_session.add(SystemSetting(key="time_display", value=new_time_val))
            else:
                time_setting.value = new_time_val
            db_session.commit()
            st.rerun()

        st.divider()
        st.subheader("📅 מחולל משמרות ריקות")
        g_date = st.date_input("יום לייצור (מייצר 24 שעות מיום זה):", date.today())
        if st.button("ייצר סלוטים ריקים לתאריך זה", type="primary"):
            for p in posts:
                curr = datetime.combine(g_date, time(0,0))
                while curr < datetime.combine(g_date, time(0,0)) + timedelta(days=1):
                    if is_time_in_range(p.active_from, p.active_to, curr.time()):
                        req = p.required_guards
                        if p.boost_guards > 0 and is_time_in_range(p.boost_from, p.boost_to, curr.time()):
                            req += p.boost_guards
                        if not db_session.query(Shift).filter_by(post_id=p.id, start_time=curr).first():
                            db_session.add(Shift(post_id=p.id, start_time=curr, 
                                               end_time=curr + timedelta(minutes=p.shift_length_minutes),
                                               required_count=req))
                    curr += timedelta(minutes=p.shift_length_minutes)
            db_session.commit()
            st.success("סלוטים נוצרו בהצלחה!")

        st.markdown('<div class="danger-zone">', unsafe_allow_html=True)
        if st.button("🗑️ מחיקת כל הסלוטים (לכל התאריכים)"):
            db_session.query(Shift).delete()
            db_session.commit()
            st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

# ==========================================
# 6. Main
# ==========================================
def main():
    db_session = SessionLocal()
    st.title("ניהול שמירות מילואים 🇮🇱")
    t1, t2, t3, t4 = st.tabs(["דשבורד 🛡️", "צילום מסך 📸", "כוח אדם 👥", "הגדרות ⚙️"])
    with t1: render_dashboard_tab(db_session)
    with t2: render_screenshot_tab(db_session)
    with t3: render_personnel_tab(db_session)
    with t4: render_settings_tab(db_session)
    db_session.close()

if __name__ == "__main__": main()

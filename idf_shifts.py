import streamlit as st
import pandas as pd
from datetime import datetime, timedelta, date, time
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Float, ForeignKey, Time, Boolean
from sqlalchemy.orm import declarative_base, sessionmaker

# ==========================================
# 0. הגדרות תצוגה ו-RTL
# ==========================================
st.set_page_config(page_title="מערכת שיבוץ - מילואים", layout="wide")

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

    /* יישור טבלאות RTL ומרכוז תוכן */
    [data-testid="stDataFrame"], [data-testid="stDataEditor"] {
        direction: rtl;
        text-align: right;
    }
    
    div[data-testid="stCellInner"] {
        text-align: right !important;
        justify-content: flex-start !important; /* flex-start with direction rtl means right */
    }

    .stButton>button { width: 100%; font-weight: bold; border-radius: 8px; }
    
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

# -------- תוספת לוגית: אילוצי עמדה --------
class PostConstraint(Base):
    __tablename__ = 'post_constraints'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'))
    post_id = Column(Integer, ForeignKey('posts.id'))
# ------------------------------------------

engine = create_engine('sqlite:///shifts_v8.db', connect_args={'check_same_thread': False})
Base.metadata.create_all(engine)
SessionLocal = sessionmaker(bind=engine)

MIN_REST_HOURS = 6

# ==========================================
# 2. פונקציות עזר ואלגוריתם שיבוץ
# ==========================================
def is_time_in_range(start, end, current):
    if start <= end:
        return start <= current <= end
    return current >= start or current <= end

def get_shift_warnings(db_session, target_date):
    start_dt = datetime.combine(target_date, time(0,0))
    end_dt = start_dt + timedelta(days=1)
    shifts = db_session.query(Shift).filter(Shift.start_time >= start_dt, Shift.start_time < end_dt).all()
    warnings = {}

    for s in shifts:
        assigned_ids = [x for x in (s.assigned_user_ids or "").split(",") if x]
        if len(assigned_ids) < s.required_count:
            warnings[s.id] = f"בעמדת {s.post_id}: חסר שומר ({len(assigned_ids)}/{s.required_count})"
        
        for uid in assigned_ids:
            u_obj = db_session.query(User).get(int(uid))
            u_name = u_obj.name if u_obj else "שומר"
            
            # בדיקת אילוצי עמדה (החייל חסום לעמדה זו)
            pc = db_session.query(PostConstraint).filter_by(user_id=int(uid), post_id=s.post_id).first()
            if pc: warnings[s.id] = f"אילוץ לשומר {u_name}: אינו מורשה לשמור בעמדה זו"
            
            # בדיקת אילוצים אישיים (שעות)
            c = db_session.query(Constraint).filter(Constraint.user_id == int(uid), Constraint.start_time < s.end_time, Constraint.end_time > s.start_time).first()
            if c: warnings[s.id] = f"אילוץ לשומר {u_name}: {c.reason}"
            
            # בדיקת מנוחה (6 שעות)
            prev_shifts_candidates = db_session.query(Shift).filter(
                Shift.end_time <= s.start_time,
                Shift.assigned_user_ids.like(f"%{uid}%")
            ).order_by(Shift.end_time.desc()).all()
            
            prev_s = next((ps for ps in prev_shifts_candidates if uid in (ps.assigned_user_ids or "").split(",")), None)
            
            if prev_s:
                rest = (s.start_time - prev_s.end_time).total_seconds() / 3600
                if rest < MIN_REST_HOURS:
                    warnings[s.id] = f"חריגת מנוחה לשומר {u_name}: {rest:.1f} ש' בלבד"
    return warnings

def auto_assign_shifts(db_session, target_date):
    start_dt = datetime.combine(target_date, time(0,0))
    end_dt = start_dt + timedelta(days=1)
    unassigned_shifts = db_session.query(Shift).filter(Shift.start_time >= start_dt, Shift.start_time < end_dt).order_by(Shift.start_time).all()
    users = db_session.query(User).all()
    
    all_shifts = db_session.query(Shift).filter(Shift.assigned_user_ids != "").all()
    user_stats = {str(u.id): {"total": 0.0, "daily": 0.0} for u in users}
    
    pairing_rules = db_session.query(PairingRule).all()
    rules_dict = {}
    for r in pairing_rules:
        rules_dict[(str(r.user1_id), str(r.user2_id))] = r.rule_type
        rules_dict[(str(r.user2_id), str(r.user1_id))] = r.rule_type
        
    post_constraints = db_session.query(PostConstraint).all()
    blocked_posts = {(pc.user_id, pc.post_id) for pc in post_constraints}
    
    for s in all_shifts:
        duration = (s.end_time - s.start_time).total_seconds() / 3600.0
        is_today = start_dt <= s.start_time < end_dt
        for uid in (s.assigned_user_ids or "").split(","):
            if uid in user_stats:
                user_stats[uid]["total"] += duration
                if is_today:
                    user_stats[uid]["daily"] += duration
    
    for shift in unassigned_shifts:
        assigned_list = [x for x in (shift.assigned_user_ids or "").split(",") if x]
        needed = shift.required_count - len(assigned_list)
        
        for _ in range(needed):
            db_session.flush()
            candidates = []
            for user in users:
                uid_str = str(user.id)
                if uid_str in assigned_list: continue
                
                # פסילה אם החייל חסום לעמדה הזו
                if (user.id, shift.post_id) in blocked_posts:
                    continue
                
                # פסילה על חפיפת זמנים
                u_s = [s for s in db_session.query(Shift).filter(Shift.start_time >= start_dt - timedelta(hours=24)).all() if str(user.id) in (s.assigned_user_ids or "").split(",")]
                if any(max(shift.start_time, s.start_time) < min(shift.end_time, s.end_time) for s in u_s if s.id != shift.id): continue
                
                # פסילה על אילוץ כללי
                if db_session.query(Constraint).filter(Constraint.user_id == user.id, Constraint.start_time < shift.end_time, Constraint.end_time > shift.start_time).first(): continue

                # התחשבות בחוקי הזוגיות
                is_anti_buddy = False
                buddy_score = 0
                for a_uid in assigned_list:
                    rule = rules_dict.get((uid_str, a_uid))
                    if rule == 'ANTI_BUDDY':
                        is_anti_buddy = True
                        break
                    elif rule == 'BUDDY':
                        buddy_score += 1
                
                if is_anti_buddy:
                    continue

                last_s = max([s for s in u_s if s.end_time <= shift.start_time], key=lambda x: x.end_time, default=None)
                rest = (shift.start_time - last_s.end_time).total_seconds() / 3600.0 if last_s else 999
                
                total_h = user_stats[uid_str]["total"]
                daily_h = user_stats[uid_str]["daily"]
                
                candidates.append({"user": user, "total": total_h, "daily": daily_h, "rest": rest, "buddy_score": buddy_score})
            
            if candidates:
                candidates.sort(key=lambda c: (c["rest"] < MIN_REST_HOURS, -c["buddy_score"], c["daily"], c["total"], -c["rest"]))
                best = candidates[0]["user"]
                best_uid = str(best.id)
                
                assigned_list.append(best_uid)
                duration = (shift.end_time - shift.start_time).total_seconds() / 3600.0
                
                user_stats[best_uid]["total"] += duration
                user_stats[best_uid]["daily"] += duration
                best.total_hours += duration
                shift.assigned_user_ids = ",".join(assigned_list)
    db_session.commit()

# ==========================================
# 3. טאב דשבורד
# ==========================================
def render_dashboard_tab(db_session):
    st.header("לוח שיבוצים מרכזי 🛡️")
    
    col_ctrl, col_date = st.columns([2,1])
    with col_date:
        selected_date = st.date_input("בחר יום:", date.today())
    with col_ctrl:
        st.write("") 
        if st.button("🤖 הפעל שיבוץ אוטומטי חכם", type="primary"):
            auto_assign_shifts(db_session, selected_date)
            st.success("השיבוץ הושלם!")
            st.rerun()

    users = db_session.query(User).all()
    posts = db_session.query(Post).all()
    id_to_name = {str(u.id): u.name for u in users}
    name_to_id = {u.name: str(u.id) for u in users}
    
    if not posts:
        st.info("נא להגדיר עמדות בטאב הגדרות.")
        return

    start_view = datetime.combine(selected_date, time(0,0))
    end_view = start_view + timedelta(days=1)
    
    warnings_dict = get_shift_warnings(db_session, selected_date)
    post_cols = st.columns(len(posts))
    
    for i, post in enumerate(posts):
        with post_cols[i]:
            st.markdown(f'<div class="post-header">{post.name}</div>', unsafe_allow_html=True)
            p_shifts = db_session.query(Shift).filter(Shift.post_id == post.id, Shift.start_time >= start_view, Shift.start_time < end_view).order_by(Shift.start_time).all()
            
            if not p_shifts:
                st.caption("אין משמרות פעילות בתאריך זה")
                continue

            data = []
            max_g = max([s.required_count for s in p_shifts])
            
            for s in p_shifts:
                err_mark = "🛑 " if s.id in warnings_dict else ""
                assigned = (s.assigned_user_ids or "").split(",")
                row = {"ID": s.id, "זמן": f"{err_mark}{s.start_time.strftime('%H:%M')} - {s.end_time.strftime('%H:%M')}"}
                for j in range(max_g):
                    row[f"שומר {j+1}"] = id_to_name.get(assigned[j] if j < len(assigned) else "", "-- פנוי --")
                data.append(row)
            
            df = pd.DataFrame(data)
            df = df.iloc[:, ::-1]  # הפוך את העמודות משמאל לימין ל-RTL
            config = {"ID": None, "זמן": st.column_config.TextColumn(disabled=True)}
            for j in range(max_g):
                config[f"שומר {j+1}"] = st.column_config.SelectboxColumn(options=["-- פנוי --"] + list(name_to_id.keys()))
            
            edited_df = st.data_editor(df.style.set_properties(**{'text-align': 'right'}), 
                                       column_config=config, hide_index=True, key=f"d_{post.id}_{selected_date}", use_container_width=True)
            
            for _, r in edited_df.iterrows():
                s_obj = db_session.query(Shift).get(r["ID"])
                u_names = [r[f"שומר {j+1}"] for j in range(max_g) if f"שומר {j+1}" in r and r[f"שומר {j+1}"] != "-- פנוי --"]
                s_obj.assigned_user_ids = ",".join([name_to_id[n] for n in u_names if n in name_to_id])

    if warnings_dict:
        st.markdown('<div class="alert-box"><strong>🚨 חריגות בלוח:</strong><br/>' + 
                    "<br/>".join([f"• {v}" for v in warnings_dict.values()]) + '</div>', unsafe_allow_html=True)
    
    st.divider()
    if st.button("💾 שמור שינויים ידניים", use_container_width=True):
        db_session.commit()
        st.success("הלוח עודכן!")
        st.rerun()

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
                if st.form_submit_button("הוסף את כולם"):
                    names = [n.strip() for n in bulk_text.replace(",", "\n").split("\n") if n.strip()]
                    for name in names:
                        if not db_session.query(User).filter_by(name=name).first():
                            db_session.add(User(name=name))
                    db_session.commit()
                    st.rerun()

    with col2:
        with st.expander("🚫 הזנת אילוץ/חוסר זמינות"):
            all_users = db_session.query(User).all()
            if all_users:
                with st.form("add_constraint_form", clear_on_submit=True):
                    u_names = [u.name for u in all_users]
                    sel_user = st.selectbox("בחר חייל:", u_names)
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
                        st.toast(f"האילוץ ל{sel_user} נשמר בהצלחה.")
                        st.rerun()
            else:
                st.warning("הוסף קודם חיילים למערכת.")

    st.divider()
    users = db_session.query(User).all()
    posts = db_session.query(Post).all()
    shifts = db_session.query(Shift).filter(Shift.assigned_user_ids != "").all()
    
    summary = []
    for u in users:
        total_real_hours = sum([(s.end_time - s.start_time).total_seconds()/3600 for s in shifts if str(u.id) in (s.assigned_user_ids or "").split(",")])
        row = {"ID": u.id, "שם": u.name, "סה\"כ שעות": round(total_real_hours, 1)}
        for p in posts:
            p_hrs = sum([(s.end_time - s.start_time).total_seconds()/3600 for s in shifts if s.post_id == p.id and str(u.id) in (s.assigned_user_ids or "").split(",")])
            row[f"שעות ב-{p.name}"] = round(p_hrs, 1)
        row["למחיקה"] = False
        summary.append(row)
    
    if summary:
        st.subheader("📊 פירוט שעות שמירה")
        df_sum = pd.DataFrame(summary)
        df_sum = df_sum.iloc[:, ::-1] # היפוך עמודות
        ed_p = st.data_editor(df_sum.style.set_properties(**{'text-align': 'right'}), hide_index=True, use_container_width=True)
        
        if st.button("💾 שמור שינויים / מחק מסומנים"):
            for _, r in ed_p.iterrows():
                u_obj = db_session.query(User).get(r["ID"])
                if r["למחיקה"]: db_session.delete(u_obj)
                else: u_obj.name = r["שם"]
            db_session.commit()
            st.rerun()

    constraints = db_session.query(Constraint).all()
    if constraints:
        with st.expander("📋 אילוצים רשומים במערכת"):
            c_data = []
            for c in constraints:
                u_n = db_session.query(User.name).filter_by(id=c.user_id).scalar()
                c_data.append({"ID": c.id, "חייל": u_n, "התחלה": c.start_time.strftime('%d/%m %H:%M'), "סיום": c.end_time.strftime('%d/%m %H:%M'), "סיבה": c.reason, "מחק": False})
            df_c = pd.DataFrame(c_data)
            df_c = df_c.iloc[:, ::-1] # היפוך עמודות
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
    st.header("הגדרות עמדות ותגבורים ⚙️")
    
    with st.expander("➕ הוספת עמדה חדשה", expanded=False):
        with st.form("add_post_form", clear_on_submit=True):
            name = st.text_input("שם העמדה")
            c1, c2 = st.columns(2)
            s_len = c1.number_input("אורך משמרת (דקות)", 120)
            base_g = c2.number_input("שומרים בבסיס", 1)
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
                                   boost_from=b_from, boost_to=b_to, boost_guards=b_extra))
                db_session.commit()
                st.rerun()

    posts = db_session.query(Post).all()
    if posts:
        st.subheader("ניהול עמדות")
        p_list = [{"ID": p.id, "שם": p.name, "משך": p.shift_length_minutes, "שומרים": p.required_guards, "למחיקה": False} for p in posts]
        df_p = pd.DataFrame(p_list)
        df_p = df_p.iloc[:, ::-1] # היפוך עמודות
        ed_p = st.data_editor(df_p.style.set_properties(**{'text-align': 'right'}), hide_index=True, use_container_width=True)
        if st.button("מחק עמדות מסומנות"):
            for _, r in ed_p.iterrows():
                if r["למחיקה"]: 
                    db_session.query(Shift).filter_by(post_id=r["ID"]).delete()
                    db_session.delete(db_session.query(Post).get(r["ID"]))
            db_session.commit()
            st.rerun()

    # -------- אזור הגדרת זוגות חמ"ד --------
    st.divider()
    st.subheader("🤝 ניהול זוגות (חמ\"ד / הפרדת כוחות)")
    users = db_session.query(User).all()
    if len(users) >= 2:
        with st.expander("➕ הוספת כלל זוגיות חדש", expanded=False):
            with st.form("add_pairing_form", clear_on_submit=True):
                u_names = [u.name for u in users]
                col_p1, col_p2, col_p3 = st.columns(3)
                u1_name = col_p1.selectbox("חייל א'", u_names, key="u1")
                u2_name = col_p2.selectbox("חייל ב'", u_names, key="u2")
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
                            st.warning("כבר קיים כלל עבור זוג זה. מחק אותו קודם כדי לעדכן סוג.")
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
            df_r = df_r.iloc[:, ::-1] # היפוך עמודות
            ed_r = st.data_editor(df_r.style.set_properties(**{'text-align': 'right'}), hide_index=True, use_container_width=True)
            if st.button("מחק כללי זוגיות מסומנים"):
                for _, row in ed_r.iterrows():
                    if row["למחיקה"]:
                        db_session.query(PairingRule).filter_by(id=row["ID"]).delete()
                db_session.commit()
                st.rerun()
    else:
        st.info("יש להוסיף לפחות 2 חיילים למערכת בכרטיסיית 'כוח אדם' כדי להגדיר זוגות.")
    
    # -------- אזור מניעת שמירה בעמדות ספציפיות --------
    st.divider()
    st.subheader("🚫 אילוצי עמדות (מניעת שמירה בעמדה)")
    if users and posts:
        with st.expander("➕ הוספת אילוץ עמדה לחייל", expanded=False):
            with st.form("add_post_constraint_form", clear_on_submit=True):
                col_u, col_p = st.columns(2)
                u_name = col_u.selectbox("בחר חייל:", [u.name for u in users])
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
                u_n = db_session.query(User).get(pc.user_id).name
                p_n = db_session.query(Post).get(pc.post_id).name
                pc_data.append({"ID": pc.id, "חייל": u_n, "עמדה חסומה": p_n, "למחיקה": False})
            
            df_pc = pd.DataFrame(pc_data)
            df_pc = df_pc.iloc[:, ::-1] # היפוך עמודות ל-RTL
            ed_pc = st.data_editor(df_pc.style.set_properties(**{'text-align': 'right'}), hide_index=True, use_container_width=True)
            if st.button("מחק אילוצי עמדה מסומנים"):
                for _, row in ed_pc.iterrows():
                    if row["למחיקה"]:
                        db_session.query(PostConstraint).filter_by(id=row["ID"]).delete()
                db_session.commit()
                st.rerun()

    # ----------------------------------------

    st.divider()
    st.subheader("📅 ייצור סלוטים")
    g_date = st.date_input("יום לייצור:", date.today())
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
    if st.button("🗑️ מחיקת כל הסלוטים"):
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
    t1, t2, t3 = st.tabs(["דשבורד 🛡️", "כוח אדם 👥", "הגדרות ⚙️"])
    with t1: render_dashboard_tab(db_session)
    with t2: render_personnel_tab(db_session)
    with t3: render_settings_tab(db_session)
    db_session.close()

if __name__ == "__main__": main()

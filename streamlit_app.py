import streamlit as st
import random
import time
from datetime import datetime, timedelta
import pytz

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="99乘法我最強 🚀",
    page_icon="🔢",
    layout="centered",
    initial_sidebar_state="collapsed",
)

# ── Firebase init (safe, non-blocking) ───────────────────────────────────────
@st.cache_resource
def init_firebase():
    """Returns (db_ref_func_or_None, ok: bool)"""
    try:
        import firebase_admin
        from firebase_admin import credentials, db as fdb

        if not firebase_admin._apps:
            key_dict = {k: v for k, v in st.secrets["firebase"].items()
                        if k != "database_url"}
            cred = credentials.Certificate(key_dict)
            firebase_admin.initialize_app(cred, {
                "databaseURL": st.secrets["firebase"]["database_url"]
            })
        return fdb, True
    except Exception as e:
        return None, False

_fdb, _firebase_ok = init_firebase()

TW_TZ = pytz.timezone("Asia/Taipei")

def get_now_tw():
    return datetime.now(TW_TZ)

# ── Firebase helpers (all safe) ───────────────────────────────────────────────
def increment_visitor():
    """
    將訪客計數 +1。

    - 用 Firebase .transaction() 做原子遞增，確保多人同時觸發時
      不會互相覆蓋、漏算。
    - 用 st.session_state["visitor_counted"] 當作旗標，確保同一個
      瀏覽器 session（同一次造訪）只會被計數一次；即使使用者在這次
      造訪中重新整理頁面、回首頁重新按一次「開始測驗」，也不會被
      重複累加。
    """
    if not _firebase_ok or _fdb is None:
        return
    if st.session_state.get("visitor_counted"):
        return
    try:
        _fdb.reference("visitors/count").transaction(lambda c: (c or 0) + 1)
        st.session_state["visitor_counted"] = True
    except Exception:
        pass

@st.cache_data(ttl=300)  # 5 分鐘快取，避免每次 rerun 都打 Firebase
def get_visitor_count():
    """
    讀取目前的訪客總數（不會遞增）。

    加上 @st.cache_data(ttl=300) 是因為：計數過一次之後，之後每次
    Streamlit rerun（例如答題、倒數計時觸發的 rerun）仍然需要顯示
    最新的訪客數字，若不加快取，每次 rerun 都會對 Firebase 發出一次
    即時讀取請求，使用人數多時會拖慢頁面、也會消耗 Firebase 的讀取
    額度。快取 5 分鐘內看到的數字可能略舊，但對「訪客人數」這種
    展示性質的數字來說可以接受。
    """
    if not _firebase_ok or _fdb is None:
        return 0
    try:
        return _fdb.reference("visitors/count").get() or 0
    except Exception:
        return 0

def save_score(name, score, correct, total, elapsed, accuracy):
    if not _firebase_ok or _fdb is None:
        return
    now = get_now_tw()
    try:
        _fdb.reference("scores").push({
            "name": name, "score": score, "correct": correct,
            "total": total, "elapsed": elapsed,
            "accuracy": round(accuracy, 1),
            "timestamp": now.isoformat(),
            "year": now.year, "month": now.month,
            "week": int(now.strftime("%V")),
        })
        get_leaderboard.clear()   # 清除快取，榜單立刻更新
    except Exception:
        pass

# ── 安全解析：時間 / 數值 ──────────────────────────────────────────────────────

def parse_timestamp(ts_str):
    """
    將 timestamp 字串安全解析為帶時區的 datetime。
    - 缺少、非字串、格式錯誤 → 回傳 None（不可用最早時間頂替，
      否則髒資料會因為「時間最早」而在同分排序中被誤判為第一名）。
    - 舊資料若沒有時區資訊，視為台灣時間（Asia/Taipei）。
    """
    if not ts_str or not isinstance(ts_str, str):
        return None
    try:
        dt = datetime.fromisoformat(ts_str)
    except (ValueError, TypeError):
        return None

    if dt.tzinfo is None:
        dt = TW_TZ.localize(dt)
    else:
        dt = dt.astimezone(TW_TZ)
    return dt


def parse_number(value):
    """
    將分數/用時等數值安全轉換為 float。
    考量舊資料可能是字串、None、bool 或其他損毀型態，
    無法解析時一律回傳 None，交由呼叫端略過該筆紀錄。
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except (ValueError, TypeError):
            return None
    return None

# ── 日曆區間排行榜 ────────────────────────────────────────────────────────────

def get_calendar_range(period, now=None):
    """
    回傳指定排行榜期間的開始時間與結束時間（皆含時區，Asia/Taipei）。
    採用「真正的日曆區間」而非固定天數的滑動視窗或裸週次比對，
    並正確處理大小月、閏年與跨年週次。
    """
    if now is None:
        now = get_now_tw()
    elif now.tzinfo is None:
        now = TW_TZ.localize(now)
    else:
        now = now.astimezone(TW_TZ)

    def day_start(d):
        return d.replace(hour=0, minute=0, second=0, microsecond=0)

    def day_end(d):
        return d.replace(hour=23, minute=59, second=59, microsecond=999999)

    if period == "本日":
        start = day_start(now)
        end   = day_end(now)

    elif period == "本週":
        monday = now - timedelta(days=now.weekday())   # 週一為一週開始
        sunday = monday + timedelta(days=6)
        start  = day_start(monday)
        end    = day_end(sunday)

    elif period == "本月":
        start = day_start(now.replace(day=1))
        if now.month == 12:
            next_month_first = now.replace(year=now.year + 1, month=1, day=1)
        else:
            next_month_first = now.replace(month=now.month + 1, day=1)
        end = day_end(next_month_first - timedelta(days=1))

    elif period == "本季":
        q_start_month = ((now.month - 1) // 3) * 3 + 1     # 1, 4, 7, 10
        start = day_start(now.replace(month=q_start_month, day=1))
        q_end_month = q_start_month + 2
        if q_end_month == 12:
            next_q_first = now.replace(year=now.year + 1, month=1, day=1)
        else:
            next_q_first = now.replace(month=q_end_month + 1, day=1)
        end = day_end(next_q_first - timedelta(days=1))

    elif period == "本年度":
        start = day_start(now.replace(month=1, day=1))
        end   = day_end(now.replace(month=12, day=31))

    else:
        # 「歷史排行」或未知 period：回傳全時間範圍（呼叫端通常不會用到）
        start = TW_TZ.localize(datetime.min.replace(year=1971))
        end   = now

    return start, end


@st.cache_data(ttl=30)   # 榜單快取 30 秒（依 period 分別快取）
def get_leaderboard(period="歷史排行"):
    """
    依期間回傳排行榜（前 10 名），依分數由高到低、同分依用時由短到長排序。
    任何 timestamp/score/elapsed 解析失敗的髒資料，一律安全略過。
    """
    if not _firebase_ok or _fdb is None:
        return []
    try:
        data = _fdb.reference("scores").get()
    except Exception:
        return []
    if not data:
        return []

    use_time_filter = (period != "歷史排行")
    if use_time_filter:
        start, end = get_calendar_range(period)

    entries = []
    for v in data.values():
        if not isinstance(v, dict):
            continue

        ts = parse_timestamp(v.get("timestamp"))
        if ts is None:
            continue

        score = parse_number(v.get("score"))
        if score is None:
            continue

        elapsed = parse_number(v.get("elapsed"))
        if elapsed is None:
            continue

        if use_time_filter and not (start <= ts <= end):
            continue

        entry = dict(v)
        entry["score"]   = score
        entry["elapsed"] = elapsed
        entry["_ts"]      = ts
        entries.append(entry)

    entries.sort(key=lambda x: (-x["score"], x["elapsed"]))
    return entries[:10]

# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Nunito:wght@400;600;700;800;900&display=swap');

html, body, [class*="css"] { font-family: 'Nunito', sans-serif; }

.stApp {
    background: linear-gradient(135deg, #fce4ec 0%, #e3f2fd 40%, #e8f5e9 75%, #fff9c4 100%);
    min-height: 100vh;
}
#MainMenu, footer, header { visibility: hidden; }
.block-container { padding-top: 1.5rem; max-width: 740px; }

.main-title {
    font-size: 2.5rem; font-weight: 900; text-align: center;
    background: linear-gradient(90deg, #f48fb1, #90caf9, #a5d6a7, #fff176);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    background-clip: text; margin-bottom: 0.2rem; line-height: 1.2;
}
.sub-title { text-align: center; color: #90a4ae; font-size: 1rem; margin-bottom: 1.2rem; }

.card {
    background: rgba(255,255,255,0.88); border-radius: 24px;
    padding: 1.8rem 2rem; margin-bottom: 1.2rem;
    box-shadow: 0 8px 32px rgba(0,0,0,0.09);
}

.question-box {
    background: linear-gradient(135deg, #bbdefb, #f8bbd0);
    border-radius: 20px; padding: 1.8rem; text-align: center;
    font-size: 2.8rem; font-weight: 900; color: #37474f;
    margin-bottom: 1.2rem; box-shadow: 0 4px 16px rgba(0,0,0,0.10);
    letter-spacing: 2px;
}

.timer-box {
    display: flex; justify-content: space-between; align-items: center;
    background: rgba(255,255,255,0.75); border-radius: 14px;
    padding: 0.7rem 1.2rem; margin-bottom: 0.8rem;
    font-size: 1.05rem; font-weight: 700;
}
.timer-urgent { color: #e53935; }

.prog-bar-wrap { background: #e0e0e0; border-radius: 99px; height: 10px; margin-bottom: 1rem; overflow: hidden; }
.prog-bar-fill { height: 10px; border-radius: 99px; background: linear-gradient(90deg,#f48fb1,#90caf9); }

.score-big { font-size: 3.8rem; font-weight: 900; text-align: center; color: #e91e63; }
.score-info { display: flex; justify-content: space-around; flex-wrap: wrap; gap: 0.7rem; margin: 1rem 0; }
.score-chip { background: #f3e5f5; border-radius: 12px; padding: 0.55rem 1rem; font-weight: 700; font-size: 1rem; color: #6a1b9a; text-align: center; }

.ans-row { border-radius: 14px; padding: 0.85rem 1.1rem; margin: 0.45rem 0; font-size: 1rem; font-weight: 600; }
.ans-correct { background: #e8f5e9; border-left: 5px solid #43a047; }
.ans-wrong   { background: #fce4ec; border-left: 5px solid #e53935; }

.lb-row { display: flex; align-items: center; gap: 0.8rem; padding: 0.65rem 0.9rem;
          border-radius: 12px; margin: 0.28rem 0; background: rgba(255,255,255,0.65); font-weight: 700; }
.lb-medal { font-size: 1.3rem; min-width: 2rem; text-align: center; }
.lb-name  { flex: 1; color: #37474f; }
.lb-score { color: #e91e63; font-size: 1.05rem; }
.lb-acc   { color: #43a047; font-size: 0.88rem; }

.visitor-badge { text-align: center; color: #78909c; font-size: 0.88rem; margin-top: 0.5rem; }

.links-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(190px,1fr)); gap: 0.55rem; margin-top: 0.5rem; }
.link-card {
    display: flex; align-items: center; gap: 0.5rem;
    padding: 0.55rem 0.9rem; background: rgba(255,255,255,0.78);
    border-radius: 12px; text-decoration: none; color: #37474f;
    font-weight: 700; font-size: 0.88rem; border: 2px solid #e0e0e0;
}
.link-card:hover { background: #bbdefb; border-color: #90caf9; color: #1565c0; }

.visitor-badge { text-align: center; color: #78909c; font-size: 0.88rem; margin-top: 0.5rem; }

.stButton > button {
    border-radius: 14px !important;
    font-family: 'Nunito', sans-serif !important;
    font-weight: 700 !important;
    font-size: 1.1rem !important;
}
</style>
""", unsafe_allow_html=True)

# ── Constants ─────────────────────────────────────────────────────────────────
LINKS = [
    ("📖", "英文測驗挑戰網", "https://english-examine.streamlit.app/"),
    ("➕", "數學測驗挑戰網", "https://math-examine.streamlit.app/"),
    ("✍️", "國語測驗挑戰網", "https://chinese-examine.streamlit.app/"),
    ("🔬", "理化測驗挑戰網", "https://science-examine.streamlit.app/"),
    ("📜", "歷史測驗挑戰網", "https://history-examine.streamlit.app/"),
    ("🏛️", "公民測驗挑戰網", "https://civics-examine.streamlit.app/"),
    ("🧬", "生物測驗挑戰網", "https://biology-examine.streamlit.app/"),
    ("🌍", "地球科學測驗網", "https://earth-science-examine.streamlit.app/"),
    ("🌏", "地理測驗挑戰網", "https://geography-examine.streamlit.app/"),
]
MEDAL = ["🥇","🥈","🥉"] + ["🔢"]*17
TIME_PER_Q = 30

def with_external_browser(url: str) -> str:
    """
    在網址加上 openExternalBrowser=1。
    LINE / 部分 App 內建瀏覽器偵測到此參數會改用手機「系統預設瀏覽器」
    （Chrome/Safari）開啟連結，避開內建 webview 對 Streamlit 這類
    重度使用 WebSocket/JS 網站常見的載入失敗問題。
    對一般瀏覽器（Chrome 等）無影響，該參數會被目標網站忽略。
    """
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}openExternalBrowser=1"

# ── Session helpers ───────────────────────────────────────────────────────────
def ss(key, default=None):
    if key not in st.session_state:
        st.session_state[key] = default
    return st.session_state[key]

def goto(page):
    st.session_state["page"] = page
    st.rerun()

def reset_quiz():
    for k in ["questions","answers","q_start_times","q_elapsed",
              "cur_q","quiz_start","streak","score_saved"]:
        st.session_state.pop(k, None)

# ── Question generation ───────────────────────────────────────────────────────
def generate_questions(n):
    pool = [(a, b) for a in range(1,10) for b in range(1,10)]
    random.shuffle(pool)
    questions = []
    for a, b in pool[:n]:
        correct = a * b
        distractors, attempts = set(), 0
        while len(distractors) < 3 and attempts < 300:
            attempts += 1
            kind = random.randint(0, 4)
            if kind == 0:
                d = correct + random.choice([-1,1,-2,2,-3,3])
            elif kind == 1:
                d = (a + random.choice([-1,1])) * b
            elif kind == 2:
                d = a * (b + random.choice([-1,1]))
            elif kind == 3:
                d = correct + random.choice([-7,-6,-5,5,6,7])
            else:
                d = random.randint(max(1, correct-12), correct+12)
            if d != correct and d > 0:
                distractors.add(d)
        options = [correct] + list(distractors)[:3]
        random.shuffle(options)
        questions.append({"a":a,"b":b,"correct":correct,"options":options})
    return questions

# ── Mnemonic ──────────────────────────────────────────────────────────────────
_ZH = {1:"一",2:"二",3:"三",4:"四",5:"五",6:"六",7:"七",8:"八",9:"九"}
_ZH2 = {
    1:"一",2:"二",3:"三",4:"四",5:"五",6:"六",7:"七",8:"八",9:"九",
    10:"十",12:"十二",14:"十四",15:"十五",16:"十六",18:"十八",
    20:"二十",21:"二十一",24:"二十四",25:"二十五",27:"二十七",
    28:"二十八",30:"三十",32:"三十二",35:"三十五",36:"三十六",
    40:"四十",42:"四十二",45:"四十五",48:"四十八",49:"四十九",
    54:"五十四",56:"五十六",63:"六十三",64:"六十四",72:"七十二",
    81:"八十一"
}
def zh_result(n):
    if n in _ZH2:
        return _ZH2[n]
    return f"{_ZH2.get(n//10*10, str(n//10)+'十')}{_ZH.get(n%10,'')}"

def mnemonic(a, b, c):
    return f"{_ZH[a]}{_ZH[b]}{zh_result(c)}，口訣：「{_ZH[a]}{_ZH[b]}得{zh_result(c)}」"

# ── Scoring ───────────────────────────────────────────────────────────────────
def calc_score(is_correct, elapsed_sec, streak_before):
    if not is_correct:
        return 0, 0
    base = max(0, TIME_PER_Q - int(elapsed_sec))
    bonus = max(0, streak_before) * 3
    return base + bonus, streak_before + 1

# ── Pages ─────────────────────────────────────────────────────────────────────
def page_home():
    st.markdown('<div class="main-title">99乘法我最強 🚀</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-title">挑戰自我，成為乘法小達人！</div>', unsafe_allow_html=True)

    with st.form("start_form", clear_on_submit=False):
        st.markdown('<div class="card">', unsafe_allow_html=True)
        name = st.text_input("👤 你的姓名", placeholder="請輸入姓名…",
                             max_chars=20, key="form_name")

        st.markdown("**📝 選擇題數**")
        num_q = st.radio("題數", [5, 10, 20], index=1,
                         format_func=lambda x: f"{x} 題",
                         horizontal=True, key="form_num_q",
                         label_visibility="collapsed")
        st.markdown("</div>", unsafe_allow_html=True)

        submitted = st.form_submit_button("🚀 開始測驗", use_container_width=True,
                                          type="primary")

    if submitted:
        if not name.strip():
            st.error("⚠️ 請先填寫姓名！")
        else:
            reset_quiz()
            st.session_state["player_name"] = name.strip()
            st.session_state["questions"] = generate_questions(num_q)
            st.session_state["answers"] = {}
            st.session_state["q_elapsed"] = {}
            st.session_state["q_start_times"] = {}
            st.session_state["cur_q"] = 0
            st.session_state["quiz_start"] = time.time()
            st.session_state["streak"] = 0
            increment_visitor()
            goto("quiz")

    # Leaderboard
    st.markdown("---")
    _render_leaderboard_section()

    # Links
    st.markdown("### 🔗 更多測驗挑戰")
    html = '<div class="links-grid">'
    for icon, label, url in LINKS:
        html += f'<a class="link-card" href="{with_external_browser(url)}" target="_blank">{icon} {label}</a>'
    html += "</div>"
    st.markdown(html, unsafe_allow_html=True)
    st.caption(
        "💡 若從 LINE／Instagram 等 App 開啟本頁面，點擊連結沒反應時，"
        "請點畫面右下角「:」選單 → 選擇「使用瀏覽器開啟」（或「在 Chrome 中開啟」），"
        "即可正常連到測驗網站。"
    )

    v = get_visitor_count()
    st.markdown(f'<div class="visitor-badge">👥 累計訪客人數：{v:,}</div>',
                unsafe_allow_html=True)


def page_quiz():
    qs  = st.session_state.get("questions", [])
    cur = st.session_state.get("cur_q", 0)
    total = len(qs)
    answers = st.session_state.setdefault("answers", {})
    streak  = st.session_state.get("streak", 0)
    q_start_times = st.session_state.setdefault("q_start_times", {})

    # Record start time for this question
    if cur not in q_start_times:
        q_start_times[cur] = time.time()

    q_start   = q_start_times[cur]
    elapsed_q = time.time() - q_start
    time_left = max(0.0, TIME_PER_Q - elapsed_q)

    total_elapsed = int(time.time() - st.session_state["quiz_start"])
    mins, secs = divmod(total_elapsed, 60)

    # ── Timer bar ─────────────────────────────────────────────────────────────
    timer_cls = "timer-urgent" if time_left <= 8 else ""
    st.markdown(f"""
    <div class="timer-box">
        <span>⏱ 總時間 {mins:02d}:{secs:02d}</span>
        <span>第 {cur+1} / {total} 題</span>
        <span class="{timer_cls}">⏳ {int(time_left)}s</span>
    </div>""", unsafe_allow_html=True)

    pct = int(cur / total * 100)
    st.markdown(f'<div class="prog-bar-wrap"><div class="prog-bar-fill" style="width:{pct}%"></div></div>',
                unsafe_allow_html=True)

    if streak >= 2:
        st.markdown(f"🔥 連續答對 **{streak}** 題！繼續加油！")

    q = qs[cur]
    st.markdown(f'<div class="question-box">{q["a"]} × {q["b"]} = ?</div>',
                unsafe_allow_html=True)

    already = cur in answers

    # ── Time-out: auto-skip ───────────────────────────────────────────────────
    if time_left <= 0 and not already:
        answers[cur] = {"chosen": None, "correct": q["correct"],
                        "elapsed": TIME_PER_Q, "points": 0, "is_correct": False}
        st.session_state["streak"] = 0
        if cur + 1 < total:
            st.session_state["cur_q"] = cur + 1
        else:
            goto("result")
        st.rerun()

    # ── Options ───────────────────────────────────────────────────────────────
    if not already:
        for opt in q["options"]:
            if st.button(f"　{opt}", key=f"opt_{cur}_{opt}", use_container_width=True):
                elapsed = time.time() - q_start
                is_cor = (opt == q["correct"])
                pts, new_streak = calc_score(is_cor, elapsed,
                                             streak if is_cor else 0)
                answers[cur] = {"chosen": opt, "correct": q["correct"],
                                 "elapsed": elapsed, "points": pts,
                                 "is_correct": is_cor}
                st.session_state["streak"] = new_streak if is_cor else 0
                # Auto-advance to next question
                if cur + 1 < total:
                    st.session_state["cur_q"] = cur + 1
                st.rerun()
    else:
        rec = answers[cur]
        for opt in q["options"]:
            if opt == rec["correct"] and opt == rec.get("chosen"):
                st.success(f"✅　{opt}　← 答對了！")
            elif opt == rec["correct"]:
                st.success(f"✅　{opt}　← 正確答案")
            elif opt == rec.get("chosen"):
                st.error(f"❌　{opt}　← 你的選擇")
            else:
                st.button(f"　{opt}", key=f"d_{cur}_{opt}",
                          use_container_width=True, disabled=True)

    # ── Nav ───────────────────────────────────────────────────────────────────
    st.markdown("")
    c1, c2, c3 = st.columns([1, 1, 1])
    if c1.button("◀ 上一題", use_container_width=True, disabled=(cur == 0)):
        st.session_state["cur_q"] = cur - 1
        st.rerun()
    if c3.button("下一題 ▶", use_container_width=True, disabled=(cur == total - 1)):
        st.session_state["cur_q"] = cur + 1
        st.rerun()

    answered = len(answers)
    if st.button(f"📨 繳交（已答 {answered}/{total} 題）",
                 use_container_width=True, type="primary"):
        for i, qq in enumerate(qs):
            if i not in answers:
                answers[i] = {"chosen": None, "correct": qq["correct"],
                               "elapsed": TIME_PER_Q, "points": 0, "is_correct": False}
        goto("result")

    # Auto-refresh every second for countdown
    time.sleep(1)
    st.rerun()


def page_result():
    qs      = st.session_state.get("questions", [])
    answers = st.session_state.get("answers", {})
    name    = st.session_state.get("player_name", "匿名")

    total_elapsed = int(time.time() - st.session_state.get("quiz_start", time.time()))
    mins, secs = divmod(total_elapsed, 60)
    elapsed_str = f"{mins:02d}:{secs:02d}"

    total   = len(qs)
    correct = sum(1 for r in answers.values() if r.get("is_correct"))
    score   = sum(r.get("points", 0) for r in answers.values())
    acc     = correct / total * 100 if total else 0

    if not st.session_state.get("score_saved"):
        save_score(name, score, correct, total, total_elapsed, acc)
        st.session_state["score_saved"] = True

    st.markdown('<div class="main-title">🎉 測驗結果</div>', unsafe_allow_html=True)
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown(f'<div class="score-big">{score} 分</div>', unsafe_allow_html=True)
    st.markdown(f"""
    <div class="score-info">
        <div class="score-chip">👤 {name}</div>
        <div class="score-chip">⏱ {elapsed_str}</div>
        <div class="score-chip">✅ {correct}/{total} 題</div>
        <div class="score-chip">🎯 {acc:.1f}%</div>
    </div>""", unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("### 📋 詳細解析")
    for i, q in enumerate(qs):
        rec = answers.get(i, {})
        is_cor = rec.get("is_correct", False)
        chosen = rec.get("chosen")
        css = "ans-correct" if is_cor else "ans-wrong"
        icon = "✅" if is_cor else "❌"
        chosen_txt = str(chosen) if chosen is not None else "（逾時未答）"
        memo = mnemonic(q["a"], q["b"], q["correct"])
        st.markdown(f"""
        <div class="ans-row {css}">
            {icon} 第{i+1}題　<b>{q['a']} × {q['b']} = ?</b><br>
            你的選擇：<b>{chosen_txt}</b>　正確答案：<b>{q['correct']}</b>　
            得分：<b>{rec.get('points',0)}</b>　用時：<b>{rec.get('elapsed',0):.1f}s</b><br>
            <small>💡 {memo}</small>
        </div>""", unsafe_allow_html=True)

    st.markdown("---")
    _render_leaderboard_section()

    if st.button("🔄 再挑戰一次", use_container_width=True, type="primary"):
        reset_quiz()
        goto("home")


def _render_leaderboard_section():
    st.markdown("### 🏆 排行榜")
    periods = ["本日", "本週", "本月", "本季", "本年度", "歷史排行"]
    tabs = st.tabs([f"{'📅' if p != '歷史排行' else '🏆'} {p}" for p in periods])
    for tab, period in zip(tabs, periods):
        with tab:
            entries = get_leaderboard(period)
            if not entries:
                st.info("目前尚無符合條件的成績紀錄，快去挑戰吧！")
            else:
                for rank, e in enumerate(entries):
                    if rank < 3:
                        medal = MEDAL[rank]
                    else:
                        medal = f"#{rank+1}"
                    st.markdown(f"""
                    <div class="lb-row">
                        <span class="lb-medal">{medal}</span>
                        <span class="lb-name">{e.get('name','—')}</span>
                        <span class="lb-score">{e.get('score',0):.0f} 分</span>
                        <span class="lb-acc">{e.get('accuracy',0):.1f}%</span>
                        <span style="color:#90a4ae;font-size:.85rem">{e.get('correct',0)}/{e.get('total',0)}</span>
                    </div>""", unsafe_allow_html=True)

# ── Router ────────────────────────────────────────────────────────────────────
page = ss("page", "home")
if page == "home":
    page_home()
elif page == "quiz":
    page_quiz()
elif page == "result":
    page_result()

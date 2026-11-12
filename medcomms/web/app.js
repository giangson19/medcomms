/**
 * MedComms speaker page.
 *
 * Talks to the front door: /api/me, /api/login, /api/signup, /api/logout,
 * /api/history, /api/feedback, /api/engine (start-up status) and the
 * /ws/<direction> session. Problems are written to the connection log; the
 * status line only describes what is happening.
 *
 * The transcript shows the current conversation, one row per sentence
 * (source and translation side by side). When a new recording starts, or the
 * direction is swapped, the rows move into History as a session.
 */

const AUDIO = {
    SAMPLE_RATE: 16000,
    CHANNELS: 1,
    CHUNK_SIZE: 640, // 40 ms at 16 kHz
    CHUNK_MS: 40,
    OPUS_BITRATE: 30000,
};
const FLAGS = { vi: '🇻🇳', en: '🇬🇧' };
const DIRECTIONS = { 'vi-en': ['vi', 'en'], 'en-vi': ['en', 'vi'] };
// Close codes after which reconnecting would not help: not signed in,
// direction unavailable or busy.
const NO_RECONNECT = new Set([4401, 1013]);
const RECONNECT_DELAYS_MS = [1000, 2000, 4000];
const SLOW_START_MS = 3000;
const READY_TIMEOUT_MS = 330000;
const FINISH_WAIT_MS = 4000; // after Stop, wait this long for the last sentence
const ENGINE_POLL_MS = 4000;
const ENGINE_POLL_LIMIT_MS = 6 * 60 * 1000;

// Typed out before anyone speaks, to show how the app behaves: each side in
// both languages (its own first), as either speaker may know only one.
const DEMO = {
    vi: { said: 'Nội dung bạn nói sẽ hiện tại đây', translated: 'Và bản dịch ở đây' },
    en: { said: 'What you say appears here', translated: 'And the translation appears here' },
};

// ------------------------------------------------------- interface language

// Everything the page says, in English and Vietnamese. Elements show a text
// by its key (data-i18n="key"; data-i18n-title / -aria / -placeholder for
// attributes), so switching language relabels the page in place.
const STRINGS = {
    en: {
        'brand.tag': 'Research prototype',
        'brand.tagline': 'Vietnamese-English Medical Speech Translation',
        'ui.switch': 'Interface language',
        'account.login': 'Log in',
        'account.signOut': 'Sign out',
        'lang.vi': 'Vietnamese',
        'lang.en': 'English',
        'stage.label': 'Translation',
        'direction.label': 'Translation direction',
        'swap': 'Swap languages',
        'mic.start': 'Start recording',
        'mic.stop': 'Stop recording',
        'clear': 'Clear',
        'clear.title': 'Move this conversation to History',
        'status.idle': 'Press the microphone to start',
        'status.connecting': 'Connecting…',
        'status.starting': 'Starting the translation server — this can take a minute or two…',
        'status.ready': 'Ready — press the microphone to start',
        'status.listening': 'Listening — speak now',
        'status.reconnecting': 'Connection lost — reconnecting…',
        'status.finishing': 'Finishing the last sentence…',
        'history': 'History',
        'history.empty': 'No conversations yet',
        'about': 'About & acknowledgements',
        'log': 'Connection log',
        'footer': '© 2025–2026 College of Engineering and Computer Science & College of Health Sciences, VinUniversity',
        'copy': 'Copy',
        'copied': 'Copied',
        'correct': 'Correct',
        'corrected': 'Corrected ✓',
        'correct.title': 'Suggest a better translation',
        'save': 'Save',
        'saved': 'Saved ✓',
        'notSaved': 'Not saved',
        'rate.transcript': 'Transcript',
        'rate.translation': 'Translation',
        'rate.aria': 'Rate the {what}',
        'rate.star': '{n} of 5',
        'auth.close': 'Close',
        'auth.username': 'Username',
        'auth.password': 'Password',
        'auth.code': 'Sign-up code',
        'auth.hint': 'Username: 3–32 lowercase letters, digits, “.”, “_” or “-”. Password: at least 8 characters.',
        'auth.login.title': 'Log in',
        'auth.login.subtitle': 'Welcome back. Log in to start translating.',
        'auth.login.submit': 'Log in',
        'auth.login.switch': 'New user? Create an account',
        'auth.signup.title': 'Create an account',
        'auth.signup.subtitle': 'Use the sign-up code you were given.',
        'auth.signup.submit': 'Create account',
        'auth.signup.switch': 'Already have an account? Log in',
        'auth.unreachable': 'Could not reach the server. Please try again.',
    },
    vi: {
        'brand.tag': 'Nguyên mẫu nghiên cứu',
        'brand.tagline': 'Dịch giọng nói y khoa Việt – Anh',
        'ui.switch': 'Ngôn ngữ giao diện',
        'account.login': 'Đăng nhập',
        'account.signOut': 'Đăng xuất',
        'lang.vi': 'Tiếng Việt',
        'lang.en': 'Tiếng Anh',
        'stage.label': 'Dịch',
        'direction.label': 'Chiều dịch',
        'swap': 'Đổi chiều dịch',
        'mic.start': 'Bắt đầu ghi âm',
        'mic.stop': 'Dừng ghi âm',
        'clear': 'Xoá',
        'clear.title': 'Chuyển cuộc hội thoại này vào Lịch sử',
        'status.idle': 'Nhấn micro để bắt đầu',
        'status.connecting': 'Đang kết nối…',
        'status.starting': 'Đang khởi động máy chủ dịch — có thể mất một đến hai phút…',
        'status.ready': 'Sẵn sàng — nhấn micro để bắt đầu',
        'status.listening': 'Đang nghe — mời bạn nói',
        'status.reconnecting': 'Mất kết nối — đang kết nối lại…',
        'status.finishing': 'Đang hoàn tất câu cuối…',
        'history': 'Lịch sử',
        'history.empty': 'Chưa có hội thoại nào',
        'about': 'Giới thiệu & ghi nhận',
        'log': 'Nhật ký kết nối',
        'footer': '© 2025–2026 Viện Kỹ thuật và Khoa học Máy tính & Viện Khoa học Sức khỏe, Trường Đại học VinUni',
        'copy': 'Sao chép',
        'copied': 'Đã chép',
        'correct': 'Sửa',
        'corrected': 'Đã sửa ✓',
        'correct.title': 'Đề xuất bản dịch tốt hơn',
        'save': 'Lưu',
        'saved': 'Đã lưu ✓',
        'notSaved': 'Chưa lưu được',
        'rate.transcript': 'Bản ghi',
        'rate.translation': 'Bản dịch',
        'rate.aria': 'Đánh giá {what}',
        'rate.star': '{n}/5',
        'auth.close': 'Đóng',
        'auth.username': 'Tên đăng nhập',
        'auth.password': 'Mật khẩu',
        'auth.code': 'Mã đăng ký',
        'auth.hint': 'Tên đăng nhập: 3–32 ký tự gồm chữ thường, chữ số, “.”, “_” hoặc “-”. Mật khẩu: ít nhất 8 ký tự.',
        'auth.login.title': 'Đăng nhập',
        'auth.login.subtitle': 'Chào mừng bạn quay lại. Đăng nhập để bắt đầu dịch.',
        'auth.login.submit': 'Đăng nhập',
        'auth.login.switch': 'Người dùng mới? Tạo tài khoản',
        'auth.signup.title': 'Tạo tài khoản',
        'auth.signup.subtitle': 'Dùng mã đăng ký bạn đã được cung cấp.',
        'auth.signup.submit': 'Tạo tài khoản',
        'auth.signup.switch': 'Đã có tài khoản? Đăng nhập',
        'auth.unreachable': 'Không kết nối được máy chủ. Vui lòng thử lại.',
    },
};

// The server's account messages (in English), for the Vietnamese page.
const SERVER_MESSAGES_VI = {
    'That sign-up code is not valid.': 'Mã đăng ký không đúng.',
    "Usernames are 3–32 characters: lowercase letters, digits, '.', '_' or '-'.":
        "Tên đăng nhập gồm 3–32 ký tự: chữ thường, chữ số, '.', '_' hoặc '-'.",
    'Passwords need at least 8 characters.': 'Mật khẩu cần ít nhất 8 ký tự.',
    'That username is taken.': 'Tên đăng nhập này đã có người dùng.',
    'Wrong username or password.': 'Sai tên đăng nhập hoặc mật khẩu.',
    'This account is disabled.': 'Tài khoản này đã bị khoá.',
};

function t(key, vars = {}) {
    const text = STRINGS[state.ui][key] ?? STRINGS.en[key] ?? key;
    return text.replace(/\{(\w+)\}/g, (_, name) => vars[name] ?? '');
}

// Shows a text by its key and remembers the key, for switching language.
function setText(element, key) {
    element.dataset.i18n = key;
    element.textContent = t(key);
}

function locale() {
    return state.ui === 'vi' ? 'vi-VN' : 'en-GB';
}

function formatTime(when) {
    return when.toLocaleTimeString(locale(), { hour: '2-digit', minute: '2-digit' });
}

function langName(code) {
    return t(`lang.${code}`);
}

function loadUiLanguage() {
    try {
        const saved = localStorage.getItem('medcomms.ui');
        if (saved in STRINGS) return saved;
    } catch (e) { /* storage unavailable */ }
    return (navigator.language || '').toLowerCase().startsWith('vi') ? 'vi' : 'en';
}

function setUiLanguage(lang) {
    state.ui = lang;
    try { localStorage.setItem('medcomms.ui', lang); } catch (e) { /* ignore */ }
    applyLanguage();
}

// Relabels everything on the page in the current interface language.
function applyLanguage() {
    document.documentElement.lang = state.ui;
    for (const node of document.querySelectorAll('[data-i18n]')) node.textContent = t(node.dataset.i18n);
    for (const node of document.querySelectorAll('[data-i18n-title]')) node.title = t(node.dataset.i18nTitle);
    for (const node of document.querySelectorAll('[data-i18n-aria]')) {
        node.setAttribute('aria-label', t(node.dataset.i18nAria));
    }
    for (const node of document.querySelectorAll('[data-i18n-placeholder]')) {
        node.placeholder = t(node.dataset.i18nPlaceholder);
    }
    for (const node of document.querySelectorAll('[data-time]')) node.textContent = formatTime(new Date(node.dataset.time));
    for (const node of document.querySelectorAll('.session-header')) {
        node.textContent = sessionLabel(new Date(node.dataset.when), node.dataset.direction);
    }
    for (const node of document.querySelectorAll('[data-ui-lang]')) node.hidden = node.dataset.uiLang !== state.ui;
    for (const button of el.uiButtons) button.setAttribute('aria-pressed', String(button.dataset.lang === state.ui));
    renderDirection();
    setStatus(state.statusKey);
}

const STATUS = {
    idle: 'status.idle',
    connecting: 'status.connecting',
    starting: 'status.starting',
    ready: 'status.ready',
    listening: 'status.listening',
    reconnecting: 'status.reconnecting',
    finishing: 'status.finishing',
};

const $ = (id) => document.getElementById(id);
const el = {
    loginBtn: $('login-btn'),
    user: $('user'),
    userName: $('user-name'),
    signOutBtn: $('sign-out-btn'),
    stage: document.querySelector('.stage'),
    langSource: $('lang-source'),
    langTarget: $('lang-target'),
    swapBtn: $('swap-btn'),
    transcript: $('transcript'),
    demoRow: $('demo-row'),
    demoSrc: $('demo-src'),
    demoTgt: $('demo-tgt'),
    demoSrcFlag: $('demo-src-flag'),
    demoTgtFlag: $('demo-tgt-flag'),
    status: $('status'),
    mic: $('mic-btn'),
    clearBtn: $('clear-btn'),
    uiButtons: document.querySelectorAll('.ui-lang button'),
    micLevel: $('mic-level'),
    history: $('history'),
    historyEmpty: $('history-empty'),
    sessions: $('sessions'),
    log: $('log-content'),
    authDialog: $('auth-dialog'),
    authForm: $('auth-form'),
    authTitle: $('auth-title'),
    authSubtitle: $('auth-subtitle'),
    authUsername: $('auth-username'),
    authPassword: $('auth-password'),
    authCode: $('auth-code'),
    authSignupFields: $('auth-signup-fields'),
    authError: $('auth-error'),
    authSubmit: $('auth-submit'),
    authSwitch: $('auth-switch'),
    authClose: $('auth-close'),
};

const state = {
    direction: loadDirection(),
    ui: loadUiLanguage(),
    statusKey: 'status.idle',
    user: null,
    signupOpen: false,
    phase: 'idle', // idle | connecting | recording | reconnecting | finishing
    ws: null,
    sessionId: null,
    conversation: null, // {when, direction} of the rows on the transcript
    encoder: null,
    audioContext: null,
    workletNode: null,
    mediaStream: null,
    pcm: new Int16Array(0),
    level: 0,
    wakeLock: null,
    pending: { source: '', target: '' },
    liveRow: null,
    demo: null, // timer of the typing preview
    enginePoll: null,
    onFinal: null, // called when a final result arrives (used by Stop)
};

// ---------------------------------------------------------------- utilities

function log(message) {
    const entry = document.createElement('div');
    entry.className = 'log-entry';
    entry.textContent = `[${new Date().toLocaleTimeString()}] ${message}`;
    el.log.append(entry);
    while (el.log.childElementCount > 300) el.log.firstElementChild.remove();
    el.log.scrollTop = el.log.scrollHeight;
}

// Waiting states (the server waking up, connecting, …) get a pulsing badge.
const WAITING = new Set([STATUS.connecting, STATUS.starting, STATUS.reconnecting, STATUS.finishing]);

function setStatus(key) {
    state.statusKey = key;
    el.status.textContent = t(key);
    el.status.classList.toggle('waiting', WAITING.has(key));
    el.status.classList.toggle('ready', key === STATUS.ready);
}

function loadDirection() {
    try {
        const saved = localStorage.getItem('medcomms.direction');
        if (saved in DIRECTIONS) return saved;
    } catch (e) { /* storage unavailable */ }
    return 'vi-en';
}

function saveDirection() {
    try { localStorage.setItem('medcomms.direction', state.direction); } catch (e) { /* ignore */ }
}

function directionLabel(direction) {
    const [src, tgt] = DIRECTIONS[direction];
    return `${FLAGS[src]} ${langName(src)} → ${FLAGS[tgt]} ${langName(tgt)}`;
}

function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

// ------------------------------------------------------------------ opus

libopus.onload = () => {
    state.encoder = new libopus.Encoder(
        AUDIO.CHANNELS, AUDIO.SAMPLE_RATE, AUDIO.OPUS_BITRATE, AUDIO.CHUNK_MS, true
    );
};

// --------------------------------------------------------------- direction

function renderDirection() {
    const [src, tgt] = DIRECTIONS[state.direction];
    el.langSource.textContent = `${FLAGS[src]} ${langName(src)}`;
    el.langTarget.textContent = `${FLAGS[tgt]} ${langName(tgt)}`;
    el.demoSrcFlag.textContent = FLAGS[src];
    el.demoTgtFlag.textContent = FLAGS[tgt];
    if (!hasRows()) startDemo();
}

function swapDirection() {
    if (state.phase !== 'idle') return;
    archiveConversation();
    state.direction = state.direction === 'vi-en' ? 'en-vi' : 'vi-en';
    saveDirection();
    renderDirection();
    el.swapBtn.classList.toggle('spin');
    if (state.user) watchEngine(); // each direction has its own server status
}

// ----------------------------------------------------------- typed preview

// Types the sample sentence, then its translation, then starts over.
function startDemo() {
    stopDemo();
    el.demoRow.hidden = false;
    const [src, tgt] = DIRECTIONS[state.direction];
    const said = `${DEMO[src].said}\n${DEMO[tgt].said}`;
    const translated = `${DEMO[tgt].translated}\n${DEMO[src].translated}`;
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
        el.demoSrc.textContent = said;
        el.demoTgt.textContent = translated;
        return;
    }
    let step = 0;
    const total = said.length + translated.length;
    const pause = 40; // ticks to rest on the full text
    state.demo = setInterval(() => {
        const n = step % (total + pause);
        el.demoSrc.textContent = said.slice(0, Math.min(n, said.length));
        el.demoTgt.textContent = n > said.length ? translated.slice(0, n - said.length) : '';
        // The cursor shows which side is "being written".
        el.demoSrc.classList.toggle('typing', n <= said.length);
        el.demoTgt.classList.toggle('typing', n > said.length && n <= total);
        step++;
    }, 55);
}

function stopDemo() {
    if (state.demo) clearInterval(state.demo);
    state.demo = null;
    el.demoRow.hidden = true;
}

// ------------------------------------------------------------------ phases

function setPhase(phase) {
    state.phase = phase;
    const recording = phase === 'recording' || phase === 'reconnecting';
    el.mic.classList.toggle('recording', recording);
    el.mic.classList.toggle('connecting', phase === 'connecting' || phase === 'finishing');
    el.stage.classList.toggle('recording', recording);
    el.mic.dataset.i18nAria = phase === 'idle' ? 'mic.start' : 'mic.stop';
    el.mic.setAttribute('aria-label', t(el.mic.dataset.i18nAria));
    el.swapBtn.disabled = phase !== 'idle';
    updateClear();
}

// "Clear" shows when there is a conversation on screen and nobody is talking.
function updateClear() {
    el.clearBtn.hidden = !(state.phase === 'idle' && hasRows());
}

function clearConversation() {
    if (state.phase !== 'idle') return;
    archiveConversation();
    startDemo();
    updateClear();
    el.history.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

// ---------------------------------------------------------------- accounts

async function initAccount() {
    renderDirection();
    let me;
    try {
        me = await (await fetch('/api/me')).json();
    } catch (error) {
        log(`Could not reach the server: ${error.message}`);
        return;
    }
    state.signupOpen = me.signup_open;
    if (!me.accounts) {
        signedIn(null);
    } else if (me.user) {
        signedIn(me.user);
    } else {
        signedOut();
    }
}

function signedIn(user) {
    state.user = user || 'local';
    el.loginBtn.hidden = true;
    el.user.hidden = !user;
    el.userName.textContent = user || '';
    setStatus(STATUS.idle);
    if (user) log(`Signed in as ${user}`);
    loadHistory();
    // Show whether the translation server is ready or still starting (asking
    // also wakes it, so it is likely ready by the time anyone speaks).
    watchEngine();
}

function signedOut() {
    state.user = null;
    el.user.hidden = true;
    el.loginBtn.hidden = false;
    setStatus(STATUS.idle); // pressing the microphone opens the login
}

async function signOut() {
    if (state.phase !== 'idle') await stopRecording();
    await fetch('/api/logout', { method: 'POST' });
    location.reload();
}

function openAuth(signup = false) {
    setAuthMode(signup);
    el.authSwitch.hidden = !state.signupOpen;
    if (typeof el.authDialog.showModal === 'function') {
        el.authDialog.showModal();
    } else {
        el.authDialog.setAttribute('open', '');
    }
    el.authUsername.focus();
}

function closeAuth() {
    if (typeof el.authDialog.close === 'function') el.authDialog.close();
    else el.authDialog.removeAttribute('open');
}

function setAuthMode(signup) {
    el.authForm.dataset.mode = signup ? 'signup' : 'login';
    el.authSignupFields.hidden = !signup;
    el.authCode.required = signup;
    el.authPassword.autocomplete = signup ? 'new-password' : 'current-password';
    const mode = signup ? 'signup' : 'login';
    setText(el.authTitle, `auth.${mode}.title`);
    setText(el.authSubtitle, `auth.${mode}.subtitle`);
    setText(el.authSubmit, `auth.${mode}.submit`);
    setText(el.authSwitch, `auth.${mode}.switch`);
    el.authError.hidden = true;
}

async function submitAuth(event) {
    event.preventDefault();
    const signup = el.authForm.dataset.mode === 'signup';
    el.authError.hidden = true;
    el.authSubmit.disabled = true;
    const body = {
        username: el.authUsername.value,
        password: el.authPassword.value,
        direction: state.direction, // the server wakes the engine
    };
    if (signup) body.code = el.authCode.value;
    try {
        const response = await fetch(signup ? '/api/signup' : '/api/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (response.ok) {
            closeAuth();
            el.authForm.reset();
            signedIn((await response.json()).user);
        } else {
            const message = (await response.text()).trim();
            el.authError.textContent = state.ui === 'vi' ? (SERVER_MESSAGES_VI[message] ?? message) : message;
            el.authError.hidden = false;
        }
    } catch (error) {
        el.authError.textContent = t('auth.unreachable');
        el.authError.hidden = false;
    } finally {
        el.authSubmit.disabled = false;
    }
}

// ---------------------------------------------------------- engine status

// Once signed in, follow the engine's start-up so users know when it is
// ready. Polling stops once it is ready, when recording starts, or after a
// few minutes.
async function watchEngine() {
    const run = Symbol('poll');
    state.enginePoll = run;
    const started = Date.now();
    while (state.enginePoll === run && state.phase === 'idle') {
        let engine = 'unavailable';
        try {
            const response = await fetch(`/api/engine?direction=${state.direction}`);
            if (response.ok) engine = (await response.json()).state;
        } catch (error) { /* treated as unavailable */ }
        if (state.enginePoll !== run || state.phase !== 'idle') return;
        if (engine === 'ready') {
            setStatus(STATUS.ready);
            log('The translation server is ready');
            return;
        }
        if (engine === 'starting') {
            setStatus(STATUS.starting);
        } else {
            setStatus(STATUS.idle);
            log(`${directionLabel(state.direction)} is not available right now`);
            return;
        }
        if (Date.now() - started > ENGINE_POLL_LIMIT_MS) return;
        await sleep(ENGINE_POLL_MS);
    }
}

// ----------------------------------------------------------------- session

// Resolves with the recorded session id (or null) once the server has
// connected to the engine, which can take minutes when it is waking up.
function waitUntilReady(ws) {
    return new Promise((resolve, reject) => {
        const slow = setTimeout(() => setStatus(STATUS.starting), SLOW_START_MS);
        const giveUp = setTimeout(
            () => finish(new Error('The translation server did not respond')), READY_TIMEOUT_MS
        );
        function finish(error, sessionId) {
            clearTimeout(slow);
            clearTimeout(giveUp);
            ws.onmessage = ws.onclose = ws.onerror = null;
            error ? reject(error) : resolve(sessionId);
        }
        ws.onmessage = (event) => {
            try {
                const msg = JSON.parse(event.data);
                if (msg.type === 'ready') finish(null, msg.session || null);
            } catch (e) { /* ignore anything else until ready */ }
        };
        ws.onclose = (event) => finish(new Error(event.reason || 'Connection closed'));
        ws.onerror = () => {}; // onclose follows with the reason
    });
}

async function openSession() {
    const scheme = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const ws = new WebSocket(`${scheme}//${location.host}/ws/${state.direction}`);
    state.sessionId = await waitUntilReady(ws);
    ws.onmessage = handleMessage;
    ws.onerror = () => {};
    ws.onclose = (event) => handleClosed(ws, event);
    state.ws = ws;
}

async function handleClosed(ws, event) {
    if (ws !== state.ws || state.phase !== 'recording') return; // stopped on purpose
    log(`Connection closed${event.reason ? `: ${event.reason}` : ''}`);
    if (NO_RECONNECT.has(event.code) || event.reason === 'session time limit reached') {
        stopRecording();
        return;
    }
    // The network dropped or the server restarted: keep the microphone on and
    // try to reconnect. Speech in the meantime is not sent.
    commitPending();
    state.ws = null;
    setPhase('reconnecting');
    setStatus(STATUS.reconnecting);
    for (const [i, delay] of RECONNECT_DELAYS_MS.entries()) {
        log(`Reconnecting (${i + 1}/${RECONNECT_DELAYS_MS.length})…`);
        await sleep(delay);
        if (state.phase !== 'reconnecting') return;
        try {
            await openSession();
            setPhase('recording');
            setStatus(STATUS.listening);
            log('Reconnected');
            return;
        } catch (error) {
            log(error.message);
        }
    }
    log('Could not reconnect');
    stopRecording();
}

function handleMessage(event) {
    let msg;
    try {
        msg = JSON.parse(event.data);
    } catch (error) {
        log(`Unreadable message: ${error.message}`);
        return;
    }
    if (msg.type === 'partial') {
        showLive(msg.transcription, msg.translation);
    } else if (msg.type === 'final') {
        removeLive();
        if (msg.transcription && msg.translation) {
            addRow(newItem({
                source: msg.transcription,
                target: msg.translation,
                direction: state.direction,
                sessionId: state.sessionId,
                sentenceId: msg.id || null,
                when: new Date(),
            }));
        }
        state.pending = { source: '', target: '' };
        if (state.onFinal) state.onFinal();
    }
}

// --------------------------------------------------------------- recording

async function startRecording() {
    if (state.phase !== 'idle') return;
    if (!state.user) {
        openAuth();
        return;
    }
    if (!state.encoder) {
        log('The audio encoder is still loading; try again in a moment');
        return;
    }
    state.enginePoll = null;
    setPhase('connecting');
    setStatus(STATUS.connecting);
    // Browsers only let audio start right after a click: create the audio
    // context now, before waiting for the server (which can take minutes).
    state.audioContext = new AudioContext({ sampleRate: AUDIO.SAMPLE_RATE });
    state.audioContext.resume().catch(() => {});
    try {
        // Ask for the microphone first, so the permission prompt is not left
        // waiting behind a slow start.
        state.mediaStream = await navigator.mediaDevices.getUserMedia({
            audio: {
                channelCount: 1,
                sampleRate: AUDIO.SAMPLE_RATE,
                echoCancellation: true,
                noiseSuppression: true,
                autoGainControl: true,
            },
        });
        await openSession();
        log(`Connected (${directionLabel(state.direction)})`);

        await state.audioContext.audioWorklet.addModule('worklet-processor.js');
        state.workletNode = new AudioWorkletNode(state.audioContext, 'pcm-processor');
        const source = state.audioContext.createMediaStreamSource(state.mediaStream);
        source.connect(state.workletNode);
        state.workletNode.connect(state.audioContext.destination);
        state.workletNode.port.onmessage = handleAudio;
        await state.audioContext.resume();

        // A new conversation: the previous one moves into History.
        archiveConversation();
        state.conversation = { when: new Date(), direction: state.direction };
        stopDemo();
        setPhase('recording');
        setStatus(STATUS.listening);
        keepScreenOn();
    } catch (error) {
        log(error.message);
        cleanUpAudio();
        const ws = state.ws;
        state.ws = null;
        if (ws) ws.close();
        setPhase('idle');
        setStatus(STATUS.idle);
    }
}

// Stop the microphone at once; if a sentence is in progress, ask the server
// to finish it and wait briefly, so it arrives complete (and can be rated).
async function stopRecording() {
    if (state.phase === 'idle' || state.phase === 'finishing') return;
    const ws = state.ws;
    cleanUpAudio();
    if (ws && ws.readyState === WebSocket.OPEN && state.pending.source) {
        setPhase('finishing');
        setStatus(STATUS.finishing);
        const finished = new Promise(resolve => { state.onFinal = resolve; });
        ws.send(JSON.stringify({ type: 'end' }));
        await Promise.race([finished, sleep(FINISH_WAIT_MS)]);
        state.onFinal = null;
    }
    setPhase('idle');
    state.ws = null;
    if (ws) ws.close();
    commitPending();
    if (!hasRows()) startDemo();
    setStatus(STATUS.idle);
    log('Recording stopped');
}

function cleanUpAudio() {
    if (state.workletNode) state.workletNode.disconnect();
    if (state.audioContext) state.audioContext.close();
    if (state.mediaStream) state.mediaStream.getTracks().forEach(t => t.stop());
    state.workletNode = state.audioContext = state.mediaStream = null;
    state.pcm = new Int16Array(0);
    state.level = 0;
    el.micLevel.style.transform = 'scale(1)';
    releaseScreen();
}

function handleAudio(event) {
    const samples = event.data;
    updateLevel(samples);
    const downsampled = downsample(samples, state.audioContext.sampleRate, AUDIO.SAMPLE_RATE);
    const pcm = new Int16Array(state.pcm.length + downsampled.length);
    pcm.set(state.pcm);
    for (let i = 0; i < downsampled.length; i++) {
        pcm[state.pcm.length + i] = Math.max(-1, Math.min(1, downsampled[i])) * 0x7fff;
    }
    let offset = 0;
    while (pcm.length - offset >= AUDIO.CHUNK_SIZE) {
        state.encoder.input(pcm.slice(offset, offset + AUDIO.CHUNK_SIZE));
        const packet = state.encoder.output();
        if (packet && state.ws && state.ws.readyState === WebSocket.OPEN) state.ws.send(packet);
        offset += AUDIO.CHUNK_SIZE;
    }
    state.pcm = pcm.slice(offset);
}

function downsample(buffer, inputRate, targetRate) {
    if (inputRate === targetRate) return buffer;
    const ratio = inputRate / targetRate;
    const result = new Float32Array(Math.round(buffer.length / ratio));
    let offset = 0;
    for (let i = 0; i < result.length; i++) {
        const next = Math.round((i + 1) * ratio);
        let sum = 0, count = 0;
        for (let j = offset; j < next && j < buffer.length; j++) {
            sum += buffer[j];
            count++;
        }
        result[i] = count ? sum / count : 0;
        offset = next;
    }
    return result;
}

// The ring around the microphone grows with the voice, so speakers can see
// they are being heard.
function updateLevel(samples) {
    let sum = 0;
    for (let i = 0; i < samples.length; i++) sum += samples[i] * samples[i];
    const rms = Math.sqrt(sum / Math.max(1, samples.length));
    state.level = Math.max(rms, state.level * 0.9);
    el.micLevel.style.transform = `scale(${1 + Math.min(0.6, state.level * 3)})`;
}

// Phones dim and may cut the microphone; keep the screen on while recording.
async function keepScreenOn() {
    try {
        if ('wakeLock' in navigator) state.wakeLock = await navigator.wakeLock.request('screen');
    } catch (error) { /* not allowed or not supported */ }
}

function releaseScreen() {
    if (state.wakeLock) state.wakeLock.release().catch(() => {});
    state.wakeLock = null;
}

document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && state.phase === 'recording') keepScreenOn();
});

// --------------------------------------------------------------- transcript

function hasRows() {
    return el.transcript.querySelector('.row:not(.demo)') !== null;
}

function scrollToEnd() {
    el.transcript.scrollTop = el.transcript.scrollHeight;
}

// The sentence being spoken: a grey row that changes until it is final.
function showLive(source, target) {
    if (source) state.pending.source = source;
    if (target) state.pending.target = target;
    if (!state.liveRow) {
        const [src, tgt] = DIRECTIONS[state.direction];
        state.liveRow = document.createElement('div');
        state.liveRow.className = 'row live';
        state.liveRow.append(half(FLAGS[src], '', 'src'), half(FLAGS[tgt], '', 'tgt'));
        el.transcript.append(state.liveRow);
    }
    const [srcText, tgtText] = state.liveRow.querySelectorAll('.text');
    srcText.textContent = state.pending.source;
    tgtText.textContent = state.pending.target;
    scrollToEnd();
}

function removeLive() {
    if (state.liveRow) state.liveRow.remove();
    state.liveRow = null;
}

// Keeps an unfinished sentence when a session ends (it cannot be rated).
function commitPending() {
    const { source, target } = state.pending;
    removeLive();
    if (source && target) {
        addRow(newItem({
            source, target, direction: state.direction,
            sessionId: state.sessionId, sentenceId: null, when: new Date(),
        }));
    }
    state.pending = { source: '', target: '' };
}

function addRow(item) {
    el.transcript.append(renderRow(item));
    scrollToEnd();
    updateClear();
}

// Moves the rows on the transcript into History, as one session.
function archiveConversation() {
    const rows = [...el.transcript.querySelectorAll('.row:not(.demo):not(.live)')];
    if (rows.length && state.conversation) {
        const card = newSessionCard(state.conversation.when, state.conversation.direction);
        rows.forEach(row => card.append(row));
    }
    state.conversation = null;
}

// ----------------------------------------------------------------- history

function newItem({ source, target, direction, sessionId, sentenceId, when,
                   transcriptionRating = 0, translationRating = 0, correction = '' }) {
    return {
        source, target, direction, sessionId, sentenceId, when,
        transcriptionRating, translationRating, correction,
    };
}

function sessionLabel(when, direction) {
    const date = when.toLocaleDateString(locale(), { day: 'numeric', month: 'short' });
    return `${date}, ${formatTime(when)} · ${directionLabel(direction)}`;
}

// One card per session, newest first.
function newSessionCard(when, direction, { atTop = true } = {}) {
    const card = document.createElement('section');
    card.className = 'session';
    const header = document.createElement('p');
    header.className = 'session-header';
    header.dataset.when = when.toISOString();
    header.dataset.direction = direction;
    header.textContent = sessionLabel(when, direction);
    card.append(header);
    if (atTop) el.sessions.prepend(card);
    else el.sessions.append(card);
    el.historyEmpty.hidden = true;
    return card;
}

// One sentence: what was said and its translation, each with its rating
// right below it (when the sentence was recorded), plus Copy and Correct.
function renderRow(item) {
    const [src, tgt] = DIRECTIONS[item.direction];
    const row = document.createElement('article');
    row.className = 'row';
    const ratable = Boolean(item.sessionId && item.sentenceId);

    const srcFoot = document.createElement('div');
    srcFoot.className = 'row-foot foot-src';
    const time = document.createElement('span');
    time.className = 'row-time';
    time.dataset.time = item.when.toISOString();
    time.textContent = formatTime(item.when);
    srcFoot.append(time);

    const tgtFoot = document.createElement('div');
    tgtFoot.className = 'row-foot foot-tgt';
    const actions = document.createElement('div');
    actions.className = 'row-actions';
    const copy = iconButton('copy', () => copyText(item.target, copy));
    actions.append(copy);

    if (ratable) {
        srcFoot.append(stars(item, 'transcriptionRating', 'rate.transcript'));
        tgtFoot.append(stars(item, 'translationRating', 'rate.translation'));
        let correction = null;
        const fix = iconButton(item.correction ? 'corrected' : 'correct', () => {
            if (!correction) {
                correction = renderCorrection(item, fix);
                row.append(correction);
            } else {
                correction.hidden = !correction.hidden;
            }
            if (!correction.hidden) correction.querySelector('textarea').focus();
        });
        fix.dataset.i18nTitle = 'correct.title';
        fix.title = t('correct.title');
        fix.classList.toggle('rated', Boolean(item.correction));
        actions.append(fix);
    }
    tgtFoot.append(actions);

    row.append(
        half(FLAGS[src], item.source, 'src'), srcFoot,
        half(FLAGS[tgt], item.target, 'tgt'), tgtFoot,
    );
    return row;
}

function half(flag, text, kind) {
    const wrap = document.createElement('div');
    wrap.className = `half half-${kind}`;
    const flagEl = document.createElement('span');
    flagEl.className = 'half-flag';
    flagEl.setAttribute('aria-hidden', 'true');
    flagEl.textContent = flag;
    const p = document.createElement('p');
    p.className = `text ${kind}`;
    p.textContent = text;
    wrap.append(flagEl, p);
    return wrap;
}

function iconButton(key, onClick) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'icon-btn';
    setText(button, key);
    button.addEventListener('click', onClick);
    return button;
}

// Five stars, saved as soon as one is tapped.
function stars(item, field, labelKey) {
    const wrap = document.createElement('div');
    wrap.className = 'rating';
    const text = document.createElement('span');
    text.className = 'rating-label';
    setText(text, labelKey);
    const group = document.createElement('div');
    group.className = 'stars';
    group.setAttribute('role', 'radiogroup');
    group.setAttribute('aria-label', t(labelKey));
    for (let i = 1; i <= 5; i++) {
        const star = document.createElement('button');
        star.type = 'button';
        star.className = 'star';
        star.textContent = '★';
        star.setAttribute('aria-label', t('rate.star', { n: i }));
        star.addEventListener('click', () => {
            item[field] = i;
            paint();
            saveFeedback(item, text);
        });
        group.append(star);
    }
    function paint() {
        [...group.children].forEach((s, index) => {
            s.classList.toggle('on', index < item[field]);
            s.setAttribute('aria-checked', String(index + 1 === item[field]));
        });
    }
    paint();
    wrap.append(text, group);
    return wrap;
}

function renderCorrection(item, fix) {
    const box = document.createElement('div');
    box.className = 'correction';
    const input = document.createElement('textarea');
    input.rows = 2;
    input.dataset.i18nPlaceholder = 'correct.title';
    input.placeholder = t('correct.title');
    input.value = item.correction || item.target;
    const save = document.createElement('button');
    save.type = 'button';
    save.className = 'btn btn-quiet';
    setText(save, 'save');
    save.addEventListener('click', async () => {
        const text = input.value.trim();
        item.correction = text === item.target ? '' : text;
        if (await saveFeedback(item, save)) {
            setText(fix, item.correction ? 'corrected' : 'correct');
            fix.classList.toggle('rated', Boolean(item.correction));
            setTimeout(() => { box.hidden = true; }, 600);
        }
    });
    box.append(input, save);
    return box;
}

// Saves the sentence's ratings and correction; ``where`` briefly says
// "Saved ✓" (in place, so nothing moves).
async function saveFeedback(item, where) {
    try {
        const response = await fetch('/api/feedback', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                session: item.sessionId,
                item: item.sentenceId,
                source: item.source,
                translation: item.target,
                transcription_rating: item.transcriptionRating,
                translation_rating: item.translationRating,
                correction: item.correction,
            }),
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        flash(where, 'saved');
        return true;
    } catch (error) {
        flash(where, 'notSaved');
        log(`Feedback not saved: ${error.message}`);
        return false;
    }
}

// Briefly shows a message in place of an element's own text.
function flash(element, messageKey) {
    element.textContent = t(messageKey);
    element.classList.add('flash');
    clearTimeout(element.flashTimer);
    element.flashTimer = setTimeout(() => {
        element.textContent = t(element.dataset.i18n);
        element.classList.remove('flash');
    }, 1500);
}

async function copyText(text, button) {
    try {
        await navigator.clipboard.writeText(text);
        flash(button, 'copied');
    } catch (error) {
        log('Could not copy');
    }
}

// Loads the user's earlier sessions (the server sends them newest first).
async function loadHistory() {
    try {
        const response = await fetch('/api/history');
        if (!response.ok) return;
        const { sessions } = await response.json();
        el.sessions.replaceChildren();
        for (const session of sessions) {
            const card = newSessionCard(new Date(session.started), session.direction, { atTop: false });
            for (const s of session.sentences) {
                card.append(renderRow(newItem({
                    source: s.transcription,
                    target: s.translation,
                    direction: session.direction,
                    sessionId: session.session,
                    sentenceId: s.id,
                    when: new Date(s.timestamp || session.started),
                    transcriptionRating: s.transcription_rating,
                    translationRating: s.translation_rating,
                    correction: s.correction,
                })));
            }
        }
    } catch (error) {
        log(`Could not load history: ${error.message}`);
    }
}

// ------------------------------------------------------------------ events

el.mic.addEventListener('click', () => {
    if (state.phase === 'idle') startRecording();
    else if (state.phase !== 'finishing') stopRecording();
});
el.swapBtn.addEventListener('click', swapDirection);
el.clearBtn.addEventListener('click', clearConversation);
el.uiButtons.forEach(button => button.addEventListener('click', () => setUiLanguage(button.dataset.lang)));
el.loginBtn.addEventListener('click', () => openAuth());
el.signOutBtn.addEventListener('click', signOut);
el.authForm.addEventListener('submit', submitAuth);
el.authSwitch.addEventListener('click', () => setAuthMode(el.authForm.dataset.mode !== 'signup'));
el.authClose.addEventListener('click', closeAuth);

applyLanguage();
initAccount();

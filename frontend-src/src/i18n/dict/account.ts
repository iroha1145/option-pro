/**
 * 登录 / 公开落地页（Login.tsx）+ 404（NotFound.tsx）。
 * Login.tsx 是产品的公开门面，营销文案（大标、特性三行、副文、脚注免责声明）需要
 * 读起来像专业交易终端的文案，而不是逐字直译；表单/错误/按钮文案保持终端一贯的简洁。
 */
import type { Dict } from './types';

export const ACCOUNT: Dict = {
  "用户名过长，请缩短后重试": ["Username is too long. Use a shorter one.", "ユーザー名が長すぎます。短くして再入力してください。"],
  "用户名包含不支持的字符，请重新输入": ["Username contains unsupported characters. Please re-enter it.", "ユーザー名に使えない文字が含まれています。再入力してください。"],
  "该用户名不可用，请换一个": ["This username is unavailable. Choose another.", "このユーザー名は使えません。別の名前を入力してください。"],
  "该用户名已被使用，请换一个": ["This username is already taken. Choose another.", "このユーザー名は使用されています。別の名前を入力してください。"],
  "密码过长，请缩短后重试": ["Password is too long. Use a shorter one.", "パスワードが長すぎます。短くして再入力してください。"],
  "密码包含不支持的字符，请重新输入": ["Password contains unsupported characters. Please re-enter it.", "パスワードに使えない文字が含まれています。再入力してください。"],
  "注册名额已满，暂不接受新账号": ["Registration is full. New accounts are not being accepted.", "登録数が上限に達しているため、新規登録を受け付けていません。"],

  /* 登录页标题 */
  '美股研究': ['US stock research', '米国株リサーチ'],
  '从这里开始。': ['Start here.', 'ここから始める。'],

  /* ---------------- L1 特性三行（FEATURES：突破雷达 / 板块透视 / 财报 AI）---------------- */
  /* 突破雷达、板块透视两个 title 已由 market.ts 统一定义（Breakout radar / Sector X-ray），
     此处只覆盖本页新增的「财报 AI」标题与三条 desc。 */
  "追踪价格突破、回踩与成交量变化。": ["Track breakouts, pullbacks and volume changes.", "価格のブレイク、リテスト、出来高の変化を確認。"],
  "比较板块涨跌、股票强弱与期权波动率。": ["Compare sector returns, stock strength and implied option volatility.", "セクターの騰落率、銘柄の強弱、オプションの予想変動率を比較。"],
  '财报 AI': ['Earnings AI', '決算 AI'],
  "查看财报日程、市场预期与相关公司的影响分析。": ["Explore earnings dates, expectations and effects on related companies.", "決算日程、市場予想、関連企業への影響を確認。"],

  /* ---------------- L1 副文 / 脚注免责声明 ---------------- */
  "汇集行情、选股、财报与新闻，帮助你跟踪美股市场。": ["Follow the US market with quotes, screening, earnings and news in one place.", "株価、銘柄スクリーニング、決算、ニュースで米国市場の動きを確認。"],

  /* ---------------- L2 登录卡：标题 / 切换 / 表单 ---------------- */
  "登录研究工作台": ["Sign in to your research desk", "リサーチ画面にログイン"],
  "登录以保存自选股，也可作为访客浏览": ["Sign in to save your watchlist, or browse as a guest", "ログインしてウォッチリストを保存、またはゲストとして閲覧"],
  '注册': ['Sign up', '新規登録'],
  '无法连接服务，登录暂不可用': [
    "Can't reach the service. Sign-in is temporarily unavailable.",
    'サービスに接続できません。サインインは一時的に利用できません。',
  ],
  '用户名': ['Username', 'ユーザー名'],
  '起一个用户名': ['Pick a username', '希望のユーザー名'],
  '密码': ['Password', 'パスワード'],
  '设置密码': ['Create a password', 'パスワードを設定'],
  '至少 15 个字符，可使用一句容易记住的长短语': [
    'Use at least 15 characters. A memorable long phrase works well.',
    '15文字以上で設定してください。覚えやすい長いフレーズも使えます。',
  ],
  '新密码至少需要 15 个字符': ['New passwords need at least 15 characters.', '新しいパスワードは15文字以上にしてください。'],
  '这个密码过于常见，请换一个较长的短语': ['This password is too common. Choose a longer phrase.', 'よく使われるパスワードです。別の長いフレーズを使ってください。'],
  '输入密码': ['Enter your password', 'パスワードを入力'],
  '隐藏密码': ['Hide password', 'パスワードを隠す'],
  '显示密码': ['Show password', 'パスワードを表示する'],
  'Caps Lock 已开启': ['Caps Lock is on', 'Caps Lock がオンです'],
  '验证中…': ['Verifying…', '検証中…'],
  '已创建': ['Created', '作成完了'],
  '验证通过': ['Verified', '確認完了'],
  '注册并登录': ['Sign up & sign in', '登録してサインイン'],
  '或': ['or', 'または'],
  '以访客身份浏览（只读）': ['Browse as a guest (read-only)', 'ゲストとして利用（閲覧のみ）'],
  "注册后可保存自选股，在不同设备上查看": ["Save your watchlist and access it across devices", "ウォッチリストを保存し、別の端末でも確認できます"],
  "登录状态保留 30 天": ["Stay signed in for 30 days", "ログイン状態を30日間保持します"],
  '返回公开研究页面': ['Back to the public research page', '公開リサーチページに戻る'],

  /* ---------------- 校验 / 错误映射（mapError） ---------------- */
  '连续登录失败，请稍后再试': [
    'Too many failed sign-in attempts. Please try again shortly.',
    'サインインの失敗が続いています。しばらくしてから再試行してください。',
  ],
  '注册过于频繁，请稍后再试': [
    'Too many sign-up attempts. Please try again shortly.',
    '登録リクエストが多すぎます。しばらくしてから再試行してください。',
  ],
  '登录需要 HTTPS': ['Sign-in requires HTTPS.', 'サインインには HTTPS が必要です。'],
  '用户名或密码不正确': ['Incorrect username or password.', 'ユーザー名またはパスワードが正しくありません。'],
  '服务暂时不可用，稍后重试': [
    'Service temporarily unavailable. Please try again shortly.',
    'サービスが一時的に利用できません。しばらくしてから再試行してください。',
  ],
  '注册失败，请重试': ['Sign-up failed. Please try again.', '登録に失敗しました。もう一度お試しください。'],
  '请输入用户名': ['Enter a username.', 'ユーザー名を入力してください。'],
  '请输入密码': ['Enter a password.', 'パスワードを入力してください。'],

  /* ---------------- 提交成功 toast ---------------- */
  '账号已创建': ['Account created', 'アカウントを作成しました'],
  '欢迎回来': ['Welcome back', 'おかえりなさい'],
  '网络连接超时，请检查网络后重试': ['The connection timed out — check your network and retry', '接続がタイムアウトしました。ネットワークを確認して再試行してください'],
  '网络连接失败，请检查网络后重试': ['The connection failed — check your network and retry', '接続に失敗しました。ネットワークを確認して再試行してください'],
  '当前会话': ['Current session', '現在のセッション'],
  '继续浏览': ['Keep browsing', 'このまま閲覧を続ける'],
  '退出并换账号': ['Sign out & switch account', 'サインアウトしてアカウントを切り替え'],
  '加载中': ['Loading', '読み込み中'],

  /* ---------------- 404（NotFound.tsx） ---------------- */
  '页面不存在': ['Page not found', 'ページが見つかりません'],
  '返回首页': ['Back to home', 'ホームに戻る'],
};

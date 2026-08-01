/**
 * 登录 / 公开落地页（Login.tsx）+ 404 与建设中占位页（NotFound.tsx、_PageStub.tsx）。
 * Login.tsx 是产品的公开门面，营销文案（大标、特性三行、副文、脚注免责声明）需要
 * 读起来像专业交易终端的文案，而不是逐字直译；表单/错误/按钮文案保持终端一贯的简洁。
 */
import type { Dict } from './types';

export const ACCOUNT: Dict = {
  /* ---------------- L1 大标（CharStagger 逐字入场，按源码顺序拆成三段）---------------- */
  /* 原文「把市场讲给你听。」拆成 把 / 市场（marker 高亮）/ 讲给你听。三段相邻渲染，
     中间无空格（中文本身不需要）。英文单词间必须有空格，故在「把」的译文尾部嵌入
     一个普通空格作为词间分隔，避免 "The" 和 "market" 连读成 "Themarket"。 */
  '把': ['The ', 'この'],
  '市场': ['market,', '市場を'],
  '讲给你听。': ['decoded.', '読み解く。'],

  /* ---------------- L1 特性三行（FEATURES：突破雷达 / 板块透视 / 财报 AI）---------------- */
  /* 突破雷达、板块透视两个 title 已由 market.ts 统一定义（Breakout radar / Sector X-ray），
     此处只覆盖本页新增的「财报 AI」标题与三条 desc。 */
  '价格越界的瞬间，已经在你的雷达上。': [
    "The instant it breaks out, it's already on your radar.",
    '価格がブレイクした瞬間、もうレーダーに映っている。',
  ],
  '热力、强度、IV 排名，一屏定位资金方向。': [
    "Heat, strength, and IV rankings on one screen — see where the money's moving.",
    'ヒート・強度・IVランキングを一画面に。資金の向かう先が見える。',
  ],
  '财报 AI': ['Earnings AI', '決算 AI'],
  '财报落地前，先看清涟漪往哪传。': [
    'Before earnings land, see where the ripples spread.',
    '決算が出る前に、その波紋の広がる先を見る。',
  ],

  /* ---------------- L1 副文 / 脚注免责声明 ---------------- */
  '突破雷达、强度选股、板块透视、财报 AI、新闻催化剂 —— 一套终端，看懂今晚的美股。数据延迟 15 分钟，仅供研究参考。': [
    "Breakout radar, strength screener, sector X-ray, earnings AI, news catalysts — one terminal to make sense of tonight's US market. Quotes delayed 15 minutes, for research purposes only.",
    'ブレイクアウト・レーダー、強度スクリーナー、セクター透視、決算 AI、ニュース・カタリスト——1つの端末で今晩の米国株を読み解く。データは15分遅延、調査参考用です。',
  ],
  '交互研究版 · 延迟行情 · 不构成投资建议 ◆': [
    'Interactive research edition · Delayed quotes · Not investment advice ◆',
    'インタラクティブ・リサーチ版 · 遅延データ · 投資助言ではありません ◆',
  ],

  /* ---------------- L2 登录卡：标题 / 切换 / 表单 ---------------- */
  '进入终端': ['Enter the terminal', '端末に入る'],
  '登录后自选股保存在账号里 · 访客可只读浏览': [
    'Sign in to save your watchlist to your account · Guests get read-only access',
    'サインインすると自選銘柄がアカウントに保存されます · ゲストは閲覧のみ',
  ],
  '注册': ['Sign up', '新規登録'],
  '无法连接服务，登录暂不可用': [
    "Can't reach the service. Sign-in is temporarily unavailable.",
    'サービスに接続できません。サインインは一時的に利用できません。',
  ],
  '用户名': ['Username', 'ユーザー名'],
  '起一个用户名': ['Pick a username', '希望のユーザー名'],
  '密码': ['Password', 'パスワード'],
  '设置密码': ['Create a password', 'パスワードを設定'],
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
  '账号只用于保存你的自选股，不改变数据权限': [
    "Your account only saves your watchlist — it doesn't change your data access.",
    'アカウントは自選銘柄の保存だけに使い、データへのアクセス権限は変わりません。',
  ],
  '登录即同意研究用途条款 · 登录状态保留 30 天': [
    "By signing in, you agree to the research-use terms · You'll stay signed in for 30 days",
    'サインインすることで、調査目的利用規約に同意したものとみなします · サインイン状態を30日間保持します',
  ],
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
  /* 「管理员已登录」只在用户名字面等于 admin 时出现，属于该演示账号的彩蛋文案；
     产品里对应的角色概念就是 Owner，按术语表不译成 Administrator / 管理者。 */
  '管理员已登录': ['Owner signed in', 'オーナーとしてサインインしました'],
  '加载中': ['Loading', '読み込み中'],

  /* ---------------- 404（NotFound.tsx） ---------------- */
  '页面不存在': ['Page not found', 'ページが見つかりません'],

  /* ---------------- 建设中占位页（_PageStub.tsx） ---------------- */
  '· 建设中': ['· Under construction', '· 準備中'],
  '· 设计文档已就绪，数据层已对接 mock': [
    '· Design spec ready, data layer wired to mocks',
    '· 設計ドキュメント準備済み、データ層はモック接続済み',
  ],
};

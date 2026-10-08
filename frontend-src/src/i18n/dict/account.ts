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
  "用户名已被使用，请换一个": ["This username is already taken. Choose another.", "このユーザー名は使用されています。別の名前を入力してください。"],
  "密码过长，请缩短后重试": ["Password is too long. Use a shorter one.", "パスワードが長すぎます。短くして再入力してください。"],
  "密码包含不支持的字符，请重新输入": ["Password contains unsupported characters. Please re-enter it.", "パスワードに使えない文字が含まれています。再入力してください。"],
  "账号名额已满，暂不开放注册": ["Registration is full. New accounts are not being accepted.", "登録数が上限に達しているため、新規登録を受け付けていません。"],

  /* 登录页标题 */
  '美股研究': ['US stock research', '米国株リサーチ'],
  '从这里开始。': ['Start here.', 'ここから始める。'],

  /* ---------------- L1 特性三行（FEATURES：突破雷达 / 板块透视 / 财报 AI）---------------- */
  /* 突破雷达、板块透视两个 title 已由 market.ts 统一定义（Breakout radar / Sector X-ray），
     此处只覆盖本页新增的「财报 AI」标题与三条 desc。 */
  '财报 AI': ['Earnings AI', '決算 AI'],

  /* ---------------- L1 副文 / 脚注免责声明 ---------------- */

  /* ---------------- L2 登录卡：标题 / 切换 / 表单 ---------------- */
  "登录账号": ["Sign in to your account", "アカウントにログイン"],
  '注册账号': ['Sign up', '新規登録'],
  '暂时连不上服务，请稍后登录': [
    "Can't reach the service. Sign-in is temporarily unavailable.",
    'サービスに接続できません。サインインは一時的に利用できません。',
  ],
  '用户名': ['Username', 'ユーザー名'],
  '设置用户名': ['Pick a username', '希望のユーザー名'],
  '密码': ['Password', 'パスワード'],
  '设置密码': ['Create a password', 'パスワードを設定'],
  '至少 15 个字符，可使用一句容易记住的长短语': [
    'Use at least 15 characters. A memorable long phrase works well.',
    '15文字以上で設定してください。覚えやすい長いフレーズも使えます。',
  ],
  '新密码至少需要 15 个字符': ['New passwords need at least 15 characters.', '新しいパスワードは15文字以上にしてください。'],
  '密码过于常见，请换个较长的短语': ['This password is too common. Choose a longer phrase.', 'よく使われるパスワードです。別の長いフレーズを使ってください。'],
  '输入密码': ['Enter your password', 'パスワードを入力'],
  '隐藏密码': ['Hide password', 'パスワードを隠す'],
  '显示密码': ['Show password', 'パスワードを表示する'],
  'Caps Lock 已开启': ['Caps Lock is on', 'Caps Lock がオンです'],
  '验证中…': ['Verifying…', '検証中…'],
  '已创建': ['Created', '作成完了'],
  '验证通过': ['Verified', '確認完了'],
  '注册并登录': ['Sign up & sign in', '登録してサインイン'],
  '或': ['or', 'または'],
  '以访客身份浏览': ['Browse as a guest', 'ゲストとして利用'],

  /* ---------------- 校验 / 错误映射（mapError） ---------------- */
  '登录失败次数较多，请稍后再试': [
    'Too many failed sign-in attempts. Please try again shortly.',
    'サインインの失敗が続いています。しばらくしてから再試行してください。',
  ],
  '注册过于频繁，请稍后再试': [
    'Too many sign-up attempts. Please try again shortly.',
    '登録リクエストが多すぎます。しばらくしてから再試行してください。',
  ],
  '登录需要 HTTPS': ['Sign-in requires HTTPS.', 'サインインには HTTPS が必要です。'],
  '请通过 HTTPS 加密网址登录': ['Sign-in requires HTTPS.', 'サインインには HTTPS が必要です。'],
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
  '切换账号': ['Switch account', 'アカウントを切り替え'],
  '加载中': ['Loading', '読み込み中'],

  /* ---------------- 404（NotFound.tsx） ---------------- */
  '无此页面': ['Page not found', 'ページが見つかりません'],
  '返回首页': ['Back to home', 'ホームに戻る'],
};

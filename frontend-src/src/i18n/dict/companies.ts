/**
 * 后端下发的中文标签：24 个板块名（backend/app/services/sectors.py）+
 * 86 家公司的展示名与一句话简介（backend/app/services/zh_names.py 的 NAMES 表）。
 *
 * 这些字符串不是源码字面量，是 API 响应里的运行时值（如 `stock.name`、`sector.name`），
 * 键照样是简体中文原文——t() 对字面量和运行时字符串一视同仁，调用方在渲染处改用
 * `t(stock.name)` 代替 `{stock.name}` 即可命中。板块/公司不在这张表里时（backend
 * 没有 zh_name 词条，字段本来就回退成裸代码）原样透传，不会显示空白或裸中文。
 */
import type { Dict } from './types';

export const COMPANIES: Dict = {
  /* 24 个板块名（sectors.py） */
  '半导体': ['Semiconductors', '半導体'],
  '软件基础设施': ['Software Infrastructure', 'ソフトウェア・インフラ'],
  'AI 与云': ['AI & Cloud', 'AI・クラウド'],
  '生物技术': ['Biotech', 'バイオテクノロジー'],
  '医疗保健': ['Healthcare', 'ヘルスケア'],
  '消费电子': ['Consumer Electronics', '民生電子機器'],
  '汽车 / EV': ['Automotive / EV', '自動車・EV'],
  '电动车供应链': ['EV Supply Chain', 'EV サプライチェーン'],
  '大型银行': ['Major Banks', '大手銀行'],
  '金融科技': ['Fintech', 'フィンテック'],
  '零售消费': ['Retail & Consumer', '小売・消費'],
  '奢侈品': ['Luxury Goods', '高級ブランド'],
  '媒体与流媒体': ['Media & Streaming', 'メディア・ストリーミング'],
  '社交与互联网': ['Social & Internet', 'ソーシャル・インターネット'],
  '能源': ['Energy', 'エネルギー'],
  '电力公用': ['Utilities', '公益事業'],
  '国防航空': ['Aerospace & Defense', '航空防衛'],
  '航空运输': ['Airlines', '航空運輸'],
  '房地产': ['Real Estate', '不動産'],
  '加密相关': ['Crypto-Related', '暗号資産関連'],
  '中概 ADR': ['China ADRs', '中国 ADR'],
  '电信': ['Telecom', '通信'],
  '工业制造': ['Industrials', '資本財'],
  '宽基 ETF': ['Broad-Market ETFs', '広範 ETF'],

  /* 86 家公司：展示名 */
  '英伟达': ['NVIDIA', 'エヌビディア'],
  '特斯拉': ['Tesla', 'テスラ'],
  '苹果': ['Apple', 'アップル'],
  '超微半导体': ['AMD', 'AMD'],
  '亚马逊': ['Amazon', 'アマゾン'],
  'Meta平台': ['Meta Platforms', 'メタ・プラットフォームズ'],
  '微软': ['Microsoft', 'マイクロソフト'],
  '谷歌': ['Alphabet (Google)', 'アルファベット（グーグル）'],
  '标普500 ETF': ['S&P 500 ETF', 'S&P500 ETF'],
  '纳指100 ETF': ['Nasdaq 100 ETF', 'ナスダック100 ETF'],
  '台积电': ['TSMC', 'TSMC（台湾積体電路製造）'],
  '博通': ['Broadcom', 'ブロードコム'],
  '阿斯麦': ['ASML', 'ASML'],
  '美光科技': ['Micron Technology', 'マイクロン・テクノロジー'],
  '英特尔': ['Intel', 'インテル'],
  'ARM控股': ['Arm Holdings', 'アーム・ホールディングス'],
  '高通': ['Qualcomm', 'クアルコム'],
  '迈威尔科技': ['Marvell Technology', 'マーベル・テクノロジー'],
  '德州仪器': ['Texas Instruments', 'テキサス・インスツルメンツ'],
  '泛林集团': ['Lam Research', 'ラムリサーチ'],
  '科磊': ['KLA Corporation', 'KLA'],
  '应用材料': ['Applied Materials', 'アプライド マテリアルズ'],
  'Salesforce': ['Salesforce', 'セールスフォース'],
  'Adobe': ['Adobe', 'アドビ'],
  '甲骨文': ['Oracle', 'オラクル'],
  'ServiceNow': ['ServiceNow', 'サービスナウ'],
  'Snowflake': ['Snowflake', 'スノーフレイク'],
  'Palantir': ['Palantir', 'パランティア'],
  'Cloudflare': ['Cloudflare', 'クラウドフレア'],
  'Palo Alto': ['Palo Alto Networks', 'パロアルトネットワークス'],
  'CrowdStrike': ['CrowdStrike', 'クラウドストライク'],
  '礼来': ['Eli Lilly', 'イーライリリー'],
  '诺和诺德': ['Novo Nordisk', 'ノボ ノルディスク'],
  '艾伯维': ['AbbVie', 'アッヴィ'],
  '安进': ['Amgen', 'アムジェン'],
  '吉利德科学': ['Gilead Sciences', 'ギリアド・サイエンシズ'],
  '福泰制药': ['Vertex Pharmaceuticals', 'バーテックス・ファーマシューティカルズ'],
  '再生元制药': ['Regeneron Pharmaceuticals', 'リジェネロン・ファーマシューティカルズ'],
  'Moderna': ['Moderna', 'モデルナ'],
  '索尼': ['Sony', 'ソニー'],
  '戴尔': ['Dell Technologies', 'デル・テクノロジーズ'],
  '惠普': ['HP Inc.', 'HP'],
  '罗技': ['Logitech', 'ロジテック'],
  '埃克森美孚': ['ExxonMobil', 'エクソンモービル'],
  '雪佛龙': ['Chevron', 'シェブロン'],
  '康菲石油': ['ConocoPhillips', 'コノコフィリップス'],
  '斯伦贝谢': ['SLB (Schlumberger)', 'SLB（旧シュルンベルジェ）'],
  'EOG资源': ['EOG Resources', 'EOG リソーシズ'],
  '马拉松石油': ['Marathon Petroleum', 'マラソン・ペトロリアム'],
  '瓦莱罗能源': ['Valero Energy', 'バレロ・エナジー'],
  '西方石油': ['Occidental Petroleum', 'オクシデンタル・ペトロリアム'],
  '德文能源': ['Devon Energy', 'デボン・エナジー'],
  '摩根大通': ['JPMorgan Chase', 'JP モルガン・チェース'],
  '高盛': ['Goldman Sachs', 'ゴールドマン・サックス'],
  '摩根士丹利': ['Morgan Stanley', 'モルガン・スタンレー'],
  'Visa': ['Visa', 'ビザ'],
  '万事达': ['Mastercard', 'マスターカード'],
  '美国银行': ['Bank of America', 'バンク・オブ・アメリカ'],
  '花旗': ['Citigroup', 'シティグループ'],
  '贝莱德': ['BlackRock', 'ブラックロック'],
  '沃尔玛': ['Walmart', 'ウォルマート'],
  '塔吉特': ['Target', 'ターゲット'],
  '耐克': ['Nike', 'ナイキ'],
  '星巴克': ['Starbucks', 'スターバックス'],
  '麦当劳': ['McDonald’s', 'マクドナルド'],
  '辉瑞': ['Pfizer', 'ファイザー'],
  '强生': ['Johnson & Johnson', 'ジョンソン・エンド・ジョンソン'],
  '联合健康': ['UnitedHealth Group', 'ユナイテッドヘルス・グループ'],
  '波音': ['Boeing', 'ボーイング'],
  '卡特彼勒': ['Caterpillar', 'キャタピラー'],
  '迪尔': ['Deere & Company', 'ディア・アンド・カンパニー'],
  '联合包裹': ['UPS', 'UPS'],
  '联邦快递': ['FedEx', 'フェデックス'],
  '拼多多': ['PDD Holdings (Pinduoduo)', 'PDD ホールディングス（拼多多）'],
  '京东': ['JD.com', 'JD.com（京東）'],
  '百度': ['Baidu', 'バイドゥ'],
  '蔚来': ['NIO', 'NIO（蔚来汽車）'],
  '理想': ['Li Auto', 'Li Auto（理想汽車）'],
  '小鹏': ['XPeng', 'シャオペン（小鵬汽車）'],
  '奈飞': ['Netflix', 'ネットフリックス'],
  '迪士尼': ['Disney', 'ディズニー'],
  '派拉蒙天舞': ['Paramount Skydance', 'パラマウント・スカイダンス'],
  '阿里巴巴': ['Alibaba', 'アリババ'],
  '好市多': ['Costco', 'コストコ'],
  'Block': ['Block', 'ブロック'],
  'TeraWulf': ['TeraWulf', 'テラウルフ'],

  /* 86 家公司：一句话简介 */
  '全球领先的AI芯片和数据中心基础设施公司，GPU和CUDA平台开发者': [
    'Leading AI chipmaker and data-center infrastructure provider; creator of the GPU and CUDA platform.',
    'AI 半導体とデータセンター基盤で世界をリードする企業。GPU と CUDA プラットフォームの開発元。',
  ],
  '电动汽车、储能系统及太阳能产品制造商，自动驾驶技术先驱': [
    'Maker of electric vehicles, energy-storage systems, and solar products; a pioneer in self-driving technology.',
    '電気自動車・蓄電システム・太陽光製品のメーカーで、自動運転技術の先駆者。',
  ],
  'iPhone、Mac、iPad等消费电子产品及iOS/macOS生态系统运营商': [
    'Maker of the iPhone, Mac, and iPad, and operator of the iOS/macOS ecosystem.',
    'iPhone・Mac・iPad などの民生電子機器を手がけ、iOS/macOS エコシステムを運営。',
  ],
  '高性能CPU和GPU芯片设计商，数据中心和游戏市场主要竞争者': [
    'Designer of high-performance CPU and GPU chips; a major competitor in the data-center and gaming markets.',
    '高性能 CPU・GPU の設計を手がけ、データセンターとゲーミング市場の主要プレーヤー。',
  ],
  '全球最大电商平台，AWS云计算服务市场领导者': [
    'The world’s largest e-commerce platform and market leader in cloud computing through AWS.',
    '世界最大のEコマース・プラットフォームで、AWS によるクラウドコンピューティング市場のリーダー。',
  ],
  'Facebook、Instagram、WhatsApp母公司，元宇宙和AI社交平台': [
    'Parent company of Facebook, Instagram, and WhatsApp; building the metaverse and AI-driven social platforms.',
    'Facebook・Instagram・WhatsApp の親会社で、メタバースと AI ソーシャルプラットフォームを展開。',
  ],
  'Windows、Office 365、Azure云平台及Copilot AI产品开发商': [
    'Developer of Windows, Office 365, the Azure cloud platform, and the Copilot family of AI products.',
    'Windows・Office 365・Azure クラウドおよび Copilot 系 AI 製品の開発元。',
  ],
  '全球最大搜索引擎，YouTube、Android及Google Cloud运营商': [
    'Operator of the world’s largest search engine, plus YouTube, Android, and Google Cloud.',
    '世界最大の検索エンジンを運営し、YouTube・Android・Google Cloud も展開。',
  ],
  '追踪标普500指数的交易所交易基金，美股大盘基准': [
    'An exchange-traded fund tracking the S&P 500 Index — the benchmark for the broad US stock market.',
    'S&P500 指数に連動する ETF。米国株式市場全体のベンチマークとして使われる。',
  ],
  '追踪纳斯达克100指数的ETF，以科技股为主': [
    'An ETF tracking the Nasdaq 100 Index, heavily weighted toward technology stocks.',
    'ナスダック100 指数に連動する ETF で、テクノロジー株の比重が大きい。',
  ],
  '全球最大半导体代工企业，先进制程芯片制造龙头': [
    'The world’s largest semiconductor foundry and the leader in advanced-node chip manufacturing.',
    '世界最大の半導体ファウンドリで、先端プロセス半導体製造のリーダー。',
  ],
  '半导体和基础设施软件解决方案供应商，网络芯片龙头': [
    'Supplier of semiconductors and infrastructure software; a leader in networking chips.',
    '半導体とインフラソフトウェアを手がけ、ネットワーク半導体のリーダー企業。',
  ],
  '全球唯一EUV光刻机供应商，半导体设备核心企业': [
    'The world’s sole supplier of EUV lithography systems — a core supplier of semiconductor manufacturing equipment.',
    '世界で唯一 EUV 露光装置を供給する、半導体製造装置の中核企業。',
  ],
  'DRAM和NAND存储芯片制造商，AI服务器HBM内存供应商': [
    'Maker of DRAM and NAND memory chips, and a supplier of HBM memory for AI servers.',
    'DRAM・NAND メモリのメーカーで、AI サーバー向け HBM メモリも供給。',
  ],
  '老牌CPU制造商，正在推进IDM 2.0代工战略转型': [
    'A longtime CPU maker now pursuing its IDM 2.0 strategy to become a chip foundry as well.',
    '老舗の CPU メーカーで、IDM 2.0 戦略によりファウンドリ事業への転換を進めている。',
  ],
  '移动芯片架构设计公司，全球智能手机处理器架构垄断者': [
    'Designer of mobile-chip architectures, with a near-monopoly on smartphone processor architecture worldwide.',
    'モバイル向け半導体アーキテクチャを設計し、世界のスマートフォン用プロセッサ・アーキテクチャをほぼ独占。',
  ],
  '移动通信芯片和5G调制解调器龙头，骁龙处理器开发商': [
    'A leader in mobile communications chips and 5G modems, and developer of the Snapdragon processor line.',
    'モバイル通信半導体と5Gモデムのリーダーで、Snapdragon プロセッサの開発元。',
  ],
  '数据中心网络芯片和存储控制器设计商': [
    'Designer of data-center networking chips and storage controllers.',
    'データセンター向けネットワーク半導体とストレージコントローラーを設計。',
  ],
  '模拟芯片和嵌入式处理器全球最大供应商': [
    'The world’s largest supplier of analog chips and embedded processors.',
    'アナログ半導体と組み込みプロセッサで世界最大の供給企業。',
  ],
  '半导体刻蚀和沉积设备制造商': [
    'Maker of semiconductor etch and deposition equipment.',
    '半導体のエッチング・成膜装置のメーカー。',
  ],
  '半导体检测和量测设备领导者': [
    'A leader in semiconductor inspection and metrology equipment.',
    '半導体の検査・計測装置におけるリーダー企業。',
  ],
  '全球最大半导体设备制造商，覆盖沉积、刻蚀、离子注入': [
    'The world’s largest semiconductor equipment maker, spanning deposition, etch, and ion implantation.',
    '世界最大の半導体製造装置メーカーで、成膜・エッチング・イオン注入まで幅広く手がける。',
  ],
  '全球最大CRM云平台，企业级SaaS服务龙头': [
    'The world’s largest CRM cloud platform and a leader in enterprise SaaS.',
    '世界最大の CRM クラウドプラットフォームで、法人向け SaaS のリーダー。',
  ],
  '创意软件和数字营销平台龙头，Photoshop/Premiere开发商': [
    'A leader in creative software and digital-marketing platforms; developer of Photoshop and Premiere.',
    'クリエイティブソフトとデジタルマーケティング分野のリーダーで、Photoshop・Premiere の開発元。',
  ],
  '企业级数据库和云基础设施服务商，AI数据中心扩张中': [
    'Provider of enterprise databases and cloud infrastructure, currently expanding its AI data centers.',
    '法人向けデータベースとクラウド基盤を提供し、AI データセンターを拡大中。',
  ],
  '企业级IT服务管理和工作流自动化SaaS平台': [
    'A SaaS platform for enterprise IT service management and workflow automation.',
    '法人向け IT サービス管理とワークフロー自動化の SaaS プラットフォーム。',
  ],
  '云原生数据仓库和数据分析平台': [
    'A cloud-native data warehouse and data-analytics platform.',
    'クラウドネイティブなデータウェアハウス・データ分析プラットフォーム。',
  ],
  '大数据分析和AI平台，服务政府和企业客户': [
    'A big-data analytics and AI platform serving government and enterprise customers.',
    'ビッグデータ分析と AI のプラットフォームで、政府機関と企業顧客にサービスを提供。',
  ],
  '全球CDN和网络安全服务商，边缘计算平台': [
    'A global CDN and network-security provider with an edge-computing platform.',
    '世界的な CDN・ネットワークセキュリティ企業で、エッジコンピューティング基盤を展開。',
  ],
  '企业网络安全龙头，零信任架构和AI安全解决方案': [
    'A leader in enterprise cybersecurity, offering zero-trust architecture and AI-driven security solutions.',
    '法人向けサイバーセキュリティのリーダーで、ゼロトラスト architecture と AI セキュリティを提供。',
  ],
  '云原生端点安全和威胁情报平台': [
    'A cloud-native endpoint-security and threat-intelligence platform.',
    'クラウドネイティブなエンドポイントセキュリティと脅威インテリジェンスのプラットフォーム。',
  ],
  '全球制药巨头，GLP-1减肥药和阿尔茨海默药物领导者': [
    'A global pharmaceutical giant and a leader in GLP-1 weight-loss drugs and Alzheimer’s treatments.',
    '世界的製薬大手で、GLP-1 減量薬とアルツハイマー治療薬のリーダー。',
  ],
  '丹麦制药公司，Ozempic/Wegovy减肥药全球龙头': [
    'A Danish pharmaceutical company and the global leader in the weight-loss drugs Ozempic and Wegovy.',
    'デンマークの製薬会社で、減量薬 Ozempic・Wegovy の世界的リーダー。',
  ],
  '免疫学和肿瘤学生物制药公司，Humira原研药企': [
    'A biopharmaceutical company focused on immunology and oncology; the original developer of Humira.',
    '免疫学・腫瘍学領域のバイオ製薬企業で、Humira の開発元。',
  ],
  '全球领先生物技术公司，骨质疏松和肿瘤药物': [
    'A leading global biotechnology company known for osteoporosis and oncology drugs.',
    '世界をリードするバイオテクノロジー企業で、骨粗鬆症・腫瘍領域の薬剤を展開。',
  ],
  '抗病毒药物龙头，HIV和丙肝治疗领域领导者': [
    'A leader in antiviral drugs, particularly in HIV and hepatitis C treatment.',
    '抗ウイルス薬のリーダーで、HIV とC型肝炎治療の分野をリード。',
  ],
  '囊性纤维化治疗药物全球垄断者': [
    'The near-exclusive global provider of cystic fibrosis treatments.',
    '嚢胞性線維症治療薬で世界をほぼ独占する企業。',
  ],
  '眼科和免疫学生物制药公司，Eylea开发商': [
    'A biopharmaceutical company in ophthalmology and immunology; developer of Eylea.',
    '眼科・免疫学領域のバイオ製薬企業で、Eylea の開発元。',
  ],
  'mRNA技术平台公司，新冠疫苗和癌症疫苗研发': [
    'An mRNA-technology platform company developing COVID-19 and cancer vaccines.',
    'mRNA 技術プラットフォームの企業で、新型コロナワクチンとがんワクチンを開発。',
  ],
  'PlayStation游戏机、影像传感器和娱乐集团': [
    'Maker of the PlayStation console and image sensors, and a broader entertainment conglomerate.',
    'PlayStation ゲーム機とイメージセンサーを手がける、総合エンターテインメント企業。',
  ],
  'PC和企业级服务器制造商，AI服务器供应商': [
    'A maker of PCs and enterprise servers, and a supplier of AI servers.',
    'PC と法人向けサーバーのメーカーで、AI サーバーも供給。',
  ],
  '个人电脑和打印机制造商': ['A maker of personal computers and printers.', 'パソコンとプリンターのメーカー。'],
  '外设设备制造商，键鼠和视频会议设备': [
    'A maker of computer peripherals, including keyboards, mice, and video-conferencing equipment.',
    'キーボード・マウスや Web 会議機器などの周辺機器メーカー。',
  ],
  '全球最大上市石油公司，上下游一体化能源巨头': [
    'The world’s largest publicly traded oil company, an integrated upstream-to-downstream energy giant.',
    '世界最大の上場石油会社で、上流から下流まで一貫体制のエネルギー大手。',
  ],
  '全球综合能源公司，石油天然气生产和炼化': [
    'A global integrated energy company engaged in oil and gas production and refining.',
    '石油・天然ガスの生産から精製まで手がける、世界的な統合エネルギー企業。',
  ],
  '独立上游石油天然气勘探生产公司': [
    'An independent upstream oil and gas exploration and production company.',
    '独立系の上流石油・天然ガス探鉱生産企業。',
  ],
  '全球最大油田服务公司，钻井和油藏技术': [
    'The world’s largest oilfield-services company, specializing in drilling and reservoir technology.',
    '世界最大の油田サービス企業で、掘削・貯留層技術を手がける。',
  ],
  '美国最大独立页岩油生产商之一': [
    'One of the largest independent shale-oil producers in the United States.',
    '米国最大級の独立系シェールオイル生産企業の一つ。',
  ],
  '美国最大独立炼油商之一': [
    'One of the largest independent oil refiners in the United States.',
    '米国最大級の独立系石油精製企業の一つ。',
  ],
  '美国独立炼油商，汽油和柴油生产': [
    'An independent US oil refiner producing gasoline and diesel.',
    '米国の独立系石油精製企業で、ガソリンと軽油を生産。',
  ],
  '油气勘探生产公司，伯克希尔重仓股': [
    'An oil and gas exploration and production company, and a major Berkshire Hathaway holding.',
    '石油・天然ガスの探鉱生産企業で、バークシャー・ハサウェイの主要保有銘柄。',
  ],
  '美国页岩油气生产商': ['A US shale oil and gas producer.', '米国のシェールオイル・ガス生産企業。'],
  '美国最大银行，投行和资产管理巨头': [
    'The largest bank in the United States, and a giant in investment banking and asset management.',
    '米国最大の銀行で、投資銀行業務と資産運用でも大手。',
  ],
  '全球顶级投行，交易和资产管理': [
    'A top-tier global investment bank in trading and asset management.',
    '世界トップクラスの投資銀行で、トレーディングと資産運用を展開。',
  ],
  '全球投行和财富管理巨头': [
    'A global giant in investment banking and wealth management.',
    '投資銀行業務とウェルスマネジメントで世界的な大手。',
  ],
  '全球最大支付网络运营商': ['Operator of the world’s largest payment network.', '世界最大の決済ネットワークを運営。'],
  '全球第二大支付网络，跨境支付技术龙头': [
    'The world’s second-largest payment network and a leader in cross-border payment technology.',
    '世界第2位の決済ネットワークで、国際決済技術のリーダー。',
  ],
  '美国第二大银行，零售银行和财富管理': [
    'The second-largest bank in the United States, spanning retail banking and wealth management.',
    '米国第2位の銀行で、リテール銀行業務とウェルスマネジメントを展開。',
  ],
  '全球性银行集团': ['A global banking group.', '世界的な銀行グループ。'],
  '全球最大资产管理公司': ['The world’s largest asset manager.', '世界最大の資産運用会社。'],
  '全球最大零售商': ['The world’s largest retailer.', '世界最大の小売企業。'],
  '美国大型零售百货连锁': ['A major US retail and department-store chain.', '米国の大手小売・百貨店チェーン。'],
  '全球最大运动品牌': ['The world’s largest sportswear brand.', '世界最大のスポーツブランド。'],
  '全球最大连锁咖啡品牌': ['The world’s largest coffeehouse chain.', '世界最大のコーヒーチェーン。'],
  '全球最大快餐连锁品牌': ['The world’s largest fast-food chain.', '世界最大のファストフードチェーン。'],
  '全球制药巨头，新冠疫苗开发商': [
    'A global pharmaceutical giant and developer of a COVID-19 vaccine.',
    '世界的製薬大手で、新型コロナワクチンの開発元。',
  ],
  '医疗器械、制药和消费品多元化巨头': [
    'A diversified giant spanning medical devices, pharmaceuticals, and consumer products.',
    '医療機器・医薬品・コンシューマー製品を手がける複合大手企業。',
  ],
  '美国最大医疗保险和健康服务公司': [
    'The largest health insurance and health-services company in the United States.',
    '米国最大の医療保険・ヘルスケアサービス企業。',
  ],
  '全球最大航空航天制造商': ['The world’s largest aerospace manufacturer.', '世界最大の航空宇宙メーカー。'],
  '全球最大工程机械和矿山设备制造商': [
    'The world’s largest maker of construction and mining equipment.',
    '世界最大の建設機械・鉱山機械メーカー。',
  ],
  '全球最大农业和工程机械制造商': [
    'The world’s largest maker of agricultural and construction machinery.',
    '世界最大の農業機械・建設機械メーカー。',
  ],
  '全球最大快递和包裹运输公司': [
    'The world’s largest package-delivery and shipping company.',
    '世界最大の小口貨物・宅配輸送企業。',
  ],
  '全球快递和物流巨头': ['A global giant in express delivery and logistics.', '世界的な速達輸送・物流大手。'],
  '中国社交电商平台，Temu海外扩张': [
    'A Chinese social-commerce platform expanding overseas through Temu.',
    '中国のソーシャルEコマース・プラットフォームで、Temu を通じて海外展開中。',
  ],
  '中国第二大电商平台，自营物流体系': [
    'China’s second-largest e-commerce platform, built on its own logistics network.',
    '中国第2位のEコマース・プラットフォームで、自社物流網を持つ。',
  ],
  '中国最大搜索引擎，AI和自动驾驶技术': [
    'China’s largest search engine, also developing AI and self-driving technology.',
    '中国最大の検索エンジンで、AI と自動運転技術も手がける。',
  ],
  '中国高端电动汽车品牌，换电模式先驱': [
    'A premium Chinese electric-vehicle brand and a pioneer of battery-swap technology.',
    '中国の高級EVブランドで、バッテリー交換方式の先駆者。',
  ],
  '中国增程式电动SUV制造商': [
    'A Chinese maker of extended-range electric SUVs.',
    'レンジエクステンダー式電動SUVを手がける中国メーカー。',
  ],
  '中国智能电动汽车制造商，自动驾驶技术': [
    'A Chinese smart-EV maker with a focus on self-driving technology.',
    '中国のスマートEVメーカーで、自動運転技術に注力。',
  ],
  '全球最大流媒体平台，原创内容制作和分发': [
    'The world’s largest streaming platform, producing and distributing original content.',
    '世界最大の動画配信プラットフォームで、オリジナルコンテンツの制作・配信を手がける。',
  ],
  '全球娱乐集团，Disney+流媒体、主题公园和影视': [
    'A global entertainment conglomerate spanning the Disney+ streaming service, theme parks, and film and TV.',
    '世界的エンターテインメント企業で、Disney+ 配信・テーマパーク・映画/TV事業を展開。',
  ],
  'Paramount Skydance，影视娱乐与流媒体集团': [
    'Paramount Skydance — a film, television, and streaming entertainment group.',
    'Paramount Skydance。映画・TV・配信を手がけるエンターテインメント企業。',
  ],
  '中国最大电商平台，阿里云和蚂蚁集团母公司': [
    'China’s largest e-commerce platform, and the parent of Alibaba Cloud with ties to Ant Group.',
    '中国最大のEコマース・プラットフォームで、アリババクラウドを傘下に持ち、アント・グループとも関係が深い。',
  ],
  '美国最大会员制仓储超市': [
    'The largest membership warehouse-club retailer in the United States.',
    '米国最大の会員制倉庫型スーパーマーケット。',
  ],
  '原 Square，Cash App 与商户支付生态运营商': [
    'Formerly Square — operator of the Cash App and a merchant-payments ecosystem.',
    '旧 Square。Cash App と加盟店決済エコシステムを運営。',
  ],
  '美国比特币矿企，聚焦低碳能源驱动的数据中心和挖矿业务': [
    'A US bitcoin-mining company focused on low-carbon-powered data centers and mining operations.',
    '米国のビットコインマイニング企業で、低炭素エネルギーによるデータセンターとマイニング事業に注力。',
  ],
};

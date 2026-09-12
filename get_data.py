import os
import yfinance as yf
import pandas as pd
import numpy as np

# 创建数据存储目录
os.makedirs('data', exist_ok=True)
os.makedirs('data/raw', exist_ok=True) # 新增：原始数据目录

# yfinance 缓存目录放到项目内，避免系统缓存目录不可写（沙箱/权限问题）
yf.set_tz_cache_location(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.yf_cache'))

# 拟合起点：1999年QQQ上市
START_DATE = "1999-03-10"

print("开始通过直连通道下载真实数据及替代品数据...")
# 定义需要的实际标的和替代标的（已用 SPY 替代 VOO）
tickers = {
    "QQQ": "QQQ",
    "SPY": "SPY",   # SPY 历史足够长，直接作为目标标的，无需拟合
    "QLD": "QLD",
    "TQQQ": "TQQQ",
    "SGOV": "SGOV",
    "VIX": "^VIX", 
    "BIL": "BIL",   # 用于回填 SGOV (2007-2020)
    "IRX": "^IRX",  # 用于回填 SGOV 早期利率 (1999-2007)
    "VOO": "VOO",   # 与 SPY 同指数，用于更新历史 VOO 文件
    "VIVAX": "VIVAX",  # 用于回填 VTV/SCHD/VYM 的早期历史 (1992 年起)
    "VYM": "VYM",      # 用于回填 SCHD (2006-2011)
    "SCHD": "SCHD",
    "VTV": "VTV",
    "CGDV": "CGDV",
    "KO": "KO"
}

raw_data = {}
for name, ticker in tickers.items():
    print(f"正在下载 {name} ({ticker})...")
    try:
        df = yf.download(ticker, start=START_DATE)
        if not df.empty:
            # 扁平化多级索引
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            
            # 1. 【新增要求】第一时间保存最原始的下载数据
            raw_file_path = f"data/raw/{name}_raw.csv"
            df.to_csv(raw_file_path)
            
            # 自适应提取列名逻辑
            if 'Adj Close' in df.columns:
                df = df[['Open', 'High', 'Low', 'Adj Close', 'Volume']].copy()
                df.rename(columns={'Adj Close': 'Close'}, inplace=True)
            else:
                df = df[['Open', 'High', 'Low', 'Close', 'Volume']].copy()
            
            raw_data[name] = df
            print(f"-> {name} 下载并保存原始数据成功，共 {len(df)} 行。")
        else:
            print(f"❌ 警告: {name} 下载返回了空数据。")
    except Exception as e:
        print(f"❌ 错误: {name} 下载失败，原因: {e}")

# 检查最核心的 QQQ 是否下载成功
if "QQQ" not in raw_data:
    print("\n[重要提示]: QQQ 数据未能下载成功，请检查您的直连网络。")
    exit()

master_index = raw_data["QQQ"].index
all_data = {name: df.reindex(master_index) for name, df in raw_data.items()}


# ----------------- 优化后的局部修补拟合函数 -----------------

def simulate_leveraged_etf(proxy_df, target_df, leverage, annual_decay=0.015):
    """
    自适应局部修补算法：
    - 上市前：使用代理标的倒推拟合。
    - 上市后：锁定真实值，若最新交易日出现临时 NaN，使用前一日真实价格 * 当日代理收益率补齐，彻底杜绝长期漂移。
    """
    daily_decay = (1 + annual_decay) ** (1 / 252) - 1
    proxy_returns = proxy_df['Close'].pct_change().fillna(0)
    simulated_returns = proxy_returns * leverage - daily_decay
    
    first_valid_date = target_df['Close'].first_valid_index()
    first_valid_idx = proxy_df.index.get_loc(first_valid_date)
    base_price = target_df.loc[first_valid_date, 'Close']
    
    # 复制真实价格序列
    prices = target_df['Close'].copy()
    
    # 1. 倒推上市前的历史（Backward Simulation）
    current_price = base_price
    for i in range(first_valid_idx - 1, -1, -1):
        date = proxy_df.index[i]
        current_price = current_price / (1 + simulated_returns.iloc[i+1])
        prices.loc[date] = current_price
        
    # 2. 局部修补上市后的 NaN（例如因延迟导致最新交易日无数据的临时修补）
    for i in range(first_valid_idx + 1, len(proxy_df)):
        date = proxy_df.index[i]
        if pd.isna(prices.iloc[i]):
            prices.iloc[i] = prices.iloc[i-1] * (1 + simulated_returns.iloc[i])
            
    return prices


def simulate_sgov(bil_df, irx_df, sgov_df):
    """
    自适应局部修补算法模拟 SGOV
    """
    irx_daily_rate = (irx_df['Close'] / 100) / 360
    irx_daily_rate = irx_daily_rate.ffill().fillna(0)
    
    # 1. 先拟合 BIL 价格
    bil_first_date = bil_df['Close'].first_valid_index()
    bil_first_idx = bil_df.index.get_loc(bil_first_date)
    
    bil_prices = bil_df['Close'].copy()
    current_price = bil_df.loc[bil_first_date, 'Close']
    for i in range(bil_first_idx - 1, -1, -1):
        current_price = current_price / (1 + irx_daily_rate.iloc[i+1])
        bil_prices.iloc[i] = current_price
        
    # 补齐 BIL 上市后的临时 NaN
    for i in range(bil_first_idx + 1, len(bil_df)):
        if pd.isna(bil_prices.iloc[i]):
            bil_prices.iloc[i] = bil_prices.iloc[i-1] * (1 + irx_daily_rate.iloc[i])
            
    # 2. 拟合 SGOV 价格
    sgov_first_date = sgov_df['Close'].first_valid_index()
    sgov_first_idx = sgov_df.index.get_loc(sgov_first_date)
    
    sgov_prices = sgov_df['Close'].copy()
    scale_factor = sgov_df.loc[sgov_first_date, 'Close'] / bil_prices.loc[sgov_first_date]
    
    # 倒推 SGOV 上市前价格
    for i in range(sgov_first_idx - 1, -1, -1):
        sgov_prices.iloc[i] = bil_prices.iloc[i] * scale_factor
        
    # 补齐 SGOV 上市后的临时 NaN (利用 BIL 的收益率)
    bil_returns = bil_prices.pct_change().fillna(0)
    for i in range(sgov_first_idx + 1, len(sgov_df)):
        date = sgov_df.index[i]
        if pd.isna(sgov_prices.iloc[i]):
            sgov_prices.iloc[i] = sgov_prices.iloc[i-1] * (1 + bil_returns.iloc[i])
            
    return sgov_prices


# ----------------- 执行合成 -----------------
print("\n开始合成历史缺失数据...")

# 1. SPY 自身历史已完整覆盖1999年至今，无需合成，直接使用原始下载数据即可。

# 2. 拟合 QLD (2x QQQ)
if "QLD" in all_data:
    all_data["QLD"]['Close'] = simulate_leveraged_etf(all_data["QQQ"], all_data["QLD"], leverage=2.0)

# 3. 拟合 TQQQ (3x QQQ)
if "TQQQ" in all_data:
    all_data["TQQQ"]['Close'] = simulate_leveraged_etf(all_data["QQQ"], all_data["TQQQ"], leverage=3.0)

# 4. 拟合 SGOV (拼接 BIL 和 利率)
if "SGOV" in all_data and "BIL" in all_data and "IRX" in all_data:
    all_data["SGOV"]['Close'] = simulate_sgov(all_data["BIL"], all_data["IRX"], all_data["SGOV"])

# 5. 拟合 VOO (与 SPY 同指数，上市前用 SPY 收益率倒推)
if "VOO" in all_data and "SPY" in all_data:
    all_data["VOO"]['Close'] = simulate_leveraged_etf(all_data["SPY"], all_data["VOO"], leverage=1.0, annual_decay=0.0)

# 6. 拟合 VYM (VIVAX 代理，作为 SCHD 的早期回填基础)
if "VYM" in all_data and "VIVAX" in all_data:
    all_data["VYM"]['Close'] = simulate_leveraged_etf(all_data["VIVAX"], all_data["VYM"], leverage=1.0, annual_decay=0.0)

# 7. 拟合 VTV (VIVAX 与 VTV 同属 Vanguard 价值指数，收益率可直接对齐)
if "VTV" in all_data and "VIVAX" in all_data:
    all_data["VTV"]['Close'] = simulate_leveraged_etf(all_data["VIVAX"], all_data["VTV"], leverage=1.0, annual_decay=0.0)

# 8. 拟合 SCHD (上市前用 VYM 的大盘高股息收益倒推)
if "SCHD" in all_data and "VYM" in all_data:
    all_data["SCHD"]['Close'] = simulate_leveraged_etf(all_data["VYM"], all_data["SCHD"], leverage=1.0, annual_decay=0.0)

# 9. 拟合 CGDV (上市前用 SCHD 的分红价值策略收益倒推)
if "CGDV" in all_data and "SCHD" in all_data:
    all_data["CGDV"]['Close'] = simulate_leveraged_etf(all_data["SCHD"], all_data["CGDV"], leverage=1.0, annual_decay=0.0)


# ----------------- 4. 更新技术指标计算 -----------------
def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period-1, adjust=False).mean()
    avg_loss = loss.ewm(com=period-1, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

# 调整目标 ETF 列表（VOO 保留以便更新历史文件，新增分红/价值类标的）
target_etfs = ["QQQ", "SPY", "QLD", "TQQQ", "SGOV", "VIX", "VOO", "VTV", "SCHD", "CGDV", "KO"]
print("\n开始计算更新后的技术指标 (RSI_6, RSI_14, MA_5, MA_10, MA_120, MA_200)...")

for etf in target_etfs:
    if etf not in all_data:
        continue
    df = all_data[etf].copy()
    
    # 填充缺失的 OHLV 字段使其结构完整
    df['Open'] = df['Open'].combine_first(df['Close'])
    df['High'] = df['High'].combine_first(df['Close'])
    df['Low'] = df['Low'].combine_first(df['Close'])
    df['Volume'] = df['Volume'].fillna(0)
    
    # 计算新指标
    df['RSI_6'] = calculate_rsi(df['Close'], 6)
    df['RSI_14'] = calculate_rsi(df['Close'], 14)
    df['MA_5'] = df['Close'].rolling(window=5).mean()
    df['MA_10'] = df['Close'].rolling(window=10).mean()
    df['MA_120'] = df['Close'].rolling(window=120).mean()
    df['MA_200'] = df['Close'].rolling(window=200).mean()
    
    # 剔除开头由于计算最长均线（MA200）需要的历史前置空白行
    df.dropna(subset=['MA_200'], inplace=True)
    
    # 保存结果
    file_path = f"data/{etf}_synthetic_daily.csv"
    df.to_csv(file_path)
    print(f"成功导出: {file_path} (数据范围: {df.index[0].strftime('%Y-%m-%d')} 至 {df.index[-1].strftime('%Y-%m-%d')})")

print("\n数据准备与修正工作全部顺利完成！")
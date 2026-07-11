# 脚本索引

- `backtest_etf_regime_dual_sleeve_5y_v2.py`：V2 双袖市场状态轮动。
- `backtest_etf_regime_dual_sleeve_5y_v21.py`：V2.1 Top3 与样本外/压力测试。
- `regime_engine_v2.py`：六指数强弱市状态机。
- `backtest_etf_dynamic_pool_5y*.py`：动态 ETF 池、MA 闸门与 RS36 对照。
- `backtest_etf_multifactor_overlay_5y.py`：流动性、低波、趋势质量和近高点因子叠加。
- `backtest_etf_momentum_rotation_3y*.py`、`backtest_etf_rs_trend_rotation.py`：早期 3 年调仓频率、冷却期与再入场实验。
- `search_etf_high_return_strategy_5y.py`、`search_etf_winner_neighborhood_5y.py`：策略搜索与邻域稳健性检查。
- `etf_rotation_v2_live.py`：实验性收盘后信号脚本，详见 `docs/KNOWN_LIMITATIONS.md`。

脚本之间部分通过动态导入复用模块，请从项目根目录运行，避免移动单个文件。

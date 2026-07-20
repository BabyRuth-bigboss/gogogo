#!/usr/bin/env python3
"""Atomically mark four frozen strategy-ensemble paper accounts and queue T+1 orders."""
from __future__ import annotations
import argparse, csv, hashlib, json, math, shutil, sys, tempfile
from pathlib import Path
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
FIXED=ROOT/'a_stock_daily_workflow/etf_rotation/paper_trading/strategy_ensemble_100w_2026-07-17'
DCA=ROOT/'a_stock_daily_workflow/etf_rotation/paper_trading/strategy_ensemble_dca_20w_3w_2026-07-17'
POLICY=ROOT/'a_stock_daily_workflow/etf_rotation/paper_trading/strategy_ensemble_execution_policy.json'
NAMES={'511360':'短融ETF','159516':'半导体设备ETF','512480':'半导体ETF','588200':'科创芯片ETF','513100':'纳指ETF','511010':'国债ETF','518880':'黄金ETF','510300':'沪深300ETF','511260':'十年国债ETF'}
STATIC={'159516':.2,'512480':.12,'588200':.08}
FEE=.00005; SLIP=.001; RATE=.08

def rows(path):
    with path.open(encoding='utf-8-sig') as f:return list(csv.DictReader(f))
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def lot(v): return int(math.floor(v/100))*100

def execution_policy():
    return json.loads(POLICY.read_text(encoding='utf-8'))['top_level_volatility_overlay']

def reference_turnover(src, nol):
    """Return the continuous-weight reference turnover for the daily execution card.

    The executable order is still built from the four paper ledgers.  This
    reference is deliberately kept separate so the report never mistakes a
    model mark-to-market drift for a completed trade.
    """
    return {
        'levered': float(pd.read_csv(src/'ensemble_equity.csv').iloc[-1].dynamic_turnover),
        'unlevered': float(pd.read_csv(nol/'all_layers_cap_one_equity.csv').iloc[-1].dynamic_turnover),
    }

def approved_event(src, day, snapshot_hash):
    """Return a reviewed event, never infer one from a mark-to-market drift.

    A component's daily target series is a backtest calculation, not an order
    instruction for the physically segregated sleeves.  An order is permitted
    only when the model-review step has written an explicit event tied to this
    exact market snapshot (scheduled dynamic/strict rebalance, or a documented
    risk event).  Missing event files deliberately mean HOLD.
    """
    path = src / 'approved_trade_event.json'
    if not path.exists():
        return None
    event = json.loads(path.read_text(encoding='utf-8'))
    if event.get('signal_date') != day or event.get('snapshot_sha256') != snapshot_hash:
        raise RuntimeError('approved event does not match this signal date/snapshot')
    if event.get('type') not in {'scheduled_dynamic_rebalance', 'scheduled_strict_rebalance', 'documented_risk_event', 'top_level_volatility_rebalance'}:
        raise RuntimeError('unrecognised approved event type')
    if event.get('type') == 'top_level_volatility_rebalance':
        required_turnover = float(event.get('required_turnover', -1))
        band = float(execution_policy()['rebalance_band_gross_turnover'])
        if required_turnover < band:
            raise RuntimeError('top-level event is below the frozen 3% turnover band')
    if not isinstance(event.get('orders'), list):
        raise RuntimeError('approved event must carry reviewed, account-specific orders')
    return event

def cancel_invalid_pending(day):
    """Cancel the historical daily-drift orders without erasing their audit trail."""
    directories = (FIXED, DCA)
    books = []
    for directory in directories:
        latest = json.loads((directory/'account_latest.json').read_text(encoding='utf-8'))
        pending = json.loads((directory/'pending_orders.json').read_text(encoding='utf-8'))
        if latest.get('as_of') != day or pending.get('as_of') != day:
            raise RuntimeError(f'{directory}: not at {day}')
        if pending.get('status') == 'CANCELLED_INVALID_DAILY_REBALANCE':
            books.append((directory, latest, pending)); continue
        if pending.get('status') != 'PENDING_NEXT_OPEN':
            raise RuntimeError(f'{directory}: unexpected pending state {pending.get("status")}')
        pending['cancelled_orders'] = pending.pop('orders', [])
        pending['orders'] = []
        pending['status'] = 'CANCELLED_INVALID_DAILY_REBALANCE'
        pending['cancelled_at'] = day
        pending['cancellation_reason'] = 'daily mark-to-market target drift is not a frozen-strategy trade signal'
        latest['pending_orders_status'] = pending['status']
        books.append((directory, latest, pending))
    for directory, latest, pending in books:
        tmp = Path(tempfile.mkdtemp(dir=directory.parent))
        (tmp/'pending_orders.json').write_text(json.dumps(pending, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        (tmp/'account_latest.json').write_text(json.dumps(latest, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        for name in ('pending_orders.json', 'account_latest.json'):
            shutil.move(str(tmp/name), directory/name)
        tmp.rmdir()
    write_hold_report(day, '历史遗留订单未应用3%顶层换手阈值且没有袖套台账，已作废；维持当前持仓。', top_level_reference(day))

def top_level_reference(day):
    src=ROOT/f'a_stock_daily_workflow/etf_rotation/backtests/strategy_ensemble_latest_{day}'
    nol=ROOT/f'a_stock_daily_workflow/etf_rotation/backtests/strategy_ensemble_no_leverage_{day}'
    if not src.exists() or not nol.exists():
        return None
    return reference_turnover(src, nol)

def write_hold_report(day, reason, turnover=None):
    """Write the four-account daily card for a non-event session."""
    accounts=[]
    for directory, kind in ((FIXED, 'fixed'), (DCA, 'dca')):
        equity=rows(directory/'equity.csv'); holdings=rows(directory/'holdings_latest.csv')
        for variant in ('levered','unlevered'):
            e=[r for r in equity if r['variant']==variant and r['date']==day][-1]
            h=[r for r in holdings if r['variant']==variant]
            accounts.append((kind,variant,e,h))
    labels={('fixed','levered'):'固定100万·杠杆',('fixed','unlevered'):'固定100万·无杠杆',('dca','levered'):'定投20万+3万·杠杆',('dca','unlevered'):'定投20万+3万·无杠杆'}
    lines=[f'# 三策略袖组合四账户模拟盘复盘（{day}）','',f'> 状态：`HOLD`；{reason}','', '| 账户 | 期初资产 | 外部入金 | 期末资产 | 当日盈亏 | 累计盈亏 | 累计收益率/XIRR | 历史峰值 | 当前/最大回撤 | 现金 | 融资 | ETF毛仓位 | 模型暴露 |','|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for kind,variant,e,_ in accounts:
        directory = FIXED if kind == 'fixed' else DCA
        history=[r for r in rows(directory/'equity.csv') if r['variant'] == variant]
        inception=1000000.0 if kind == 'fixed' else 200000.0
        peak=max(inception, *(float(r['ending_equity']) for r in history))
        current_dd=float(e['ending_equity']) / peak - 1
        max_dd=min(float(r.get('max_drawdown') or 0) for r in history) if kind == 'dca' else min(float(r['ending_equity']) / max(inception, *(float(x['ending_equity']) for x in history[:i+1])) - 1 for i,r in enumerate(history))
        ret=float(e['nav'])-1
        ret_text=f'{ret:+.2%}' + (' / XIRR暂不适用' if kind=='dca' else '')
        lines.append(f"| {labels[(kind,variant)]} | {float(e['starting_equity']):,.2f}元 | {float(e.get('external_contribution',0) or 0):,.2f}元 | {float(e['ending_equity']):,.2f}元 | {float(e['daily_pnl']):+,.2f}元 | {float(e['cumulative_pnl']):+,.2f}元 | {ret_text} | {peak:,.2f}元 | {current_dd:+.2%} / {max_dd:+.2%} | {float(e['cash']):,.2f}元 | {float(e.get('financing_balance',0) or 0):,.2f}元 | {float(e['gross_etf_exposure']):.2%} | {float(e['model_exposure']):.4f}倍 |")
    policy=execution_policy()
    lines += ['', '## 顶层波动率执行规则', '', f"- 每日收盘测算40日组合波动率，目标年化24%，暴露每次仅向目标平滑10%，下一交易日开盘才可执行。", f"- 顶层总换手达到 **{float(policy['rebalance_band_gross_turnover']):.0%}** 净值才调仓；低于阈值只记录信号，不产生订单。", f"- 本日连续权重参考换手：杠杆版 {turnover['levered']:.2%}、无杠杆版 {turnover['unlevered']:.2%}。" if turnover else '- 本日未能读取连续权重参考换手；保留HOLD状态并等待下一次完整快照。', '', '## 成本拆分', '', '| 账户 | 当日佣金 | 当日滑点 | 当日融资成本 |','|---|---:|---:|---:|', *[f'| {labels[(kind,variant)]} | 0元 | 0元 | 0元 |' for kind,variant,_,_ in accounts], '', '## 收盘持仓', '']
    for kind,variant,_,holding in accounts:
        detail='；'.join(f"{r['code']} {r['shares']}份" if r['code'] != 'CASH' else f"现金 {float(r['market_value']):,.2f}元" for r in holding)
        lines += [f"### {labels[(kind,variant)]}", '', detail, '']
    snapshot=json.loads((FIXED/'account_latest.json').read_text(encoding='utf-8'))['snapshot_sha256']
    lines += ['## 明日操作卡', '', '**下一交易日：2026-07-21。** 四账户均为：**维持持仓，无交易。**', '', '仅在同一行情快照下存在已审核的计划调仓（动态袖双周、严格核心月频）或明确记录的风控事件时，才生成T+1订单；长持纳指、黄金与现金袖不因价格漂移回补。', '', '定投账户7月不重复入金；下次外部入金为2026-08-03，每账户30,000元。', '', f'快照SHA-256：`{snapshot}`', '', '历史回测及纸面模拟不代表未来收益，不构成投资建议。', '']
    report='\n'.join(lines)
    for directory in (FIXED,DCA):
        (directory/'daily').mkdir(exist_ok=True)
        (directory/'daily'/f'三策略袖组合模拟盘复盘_{day}.md').write_text(report,encoding='utf-8')
    note=Path('/Users/yansenz/Documents/note'); note.mkdir(parents=True,exist_ok=True)
    (note/f'三策略袖组合模拟盘复盘_{day}.md').write_text(report,encoding='utf-8')
def main(day):
    src=ROOT/f'a_stock_daily_workflow/etf_rotation/backtests/strategy_ensemble_latest_{day}'
    nol=ROOT/f'a_stock_daily_workflow/etf_rotation/backtests/strategy_ensemble_no_leverage_{day}'
    val=json.loads((src/'validation.json').read_text())
    if val['status']!='passed' or val['data_end']!=day: raise RuntimeError('model validation failed')
    snap=json.loads((src/f'market_snapshot_{day}.json').read_text())
    prices={c:float(snap['histories'][c][-1]['close']) for c in NAMES if c in snap['histories']}
    if set(NAMES)-{'511260'}-set(prices): raise RuntimeError('missing holding price')
    snapshot_hash=sha(src/f'market_snapshot_{day}.json')
    event=approved_event(src, day, snapshot_hash)
    le=float(pd.read_csv(src/'ensemble_equity.csv').iloc[-1].exposure)
    un=float(pd.read_csv(nol/'all_layers_cap_one_equity.csv').iloc[-1].exposure)
    turnover=reference_turnover(src, nol)
    band=float(execution_policy()['rebalance_band_gross_turnover'])
    all_results=[]; reports=[]
    for directory,kind,exposure in ((FIXED,'fixed',le),(DCA,'dca',le)):
      hold=rows(directory/'holdings_latest.csv'); eq=rows(directory/'equity.csv'); latest=json.loads((directory/'account_latest.json').read_text())
      asof=max(r['date'] for r in eq)
      if asof>=day: raise RuntimeError(f'{directory}: already current')
      top_level_below_band = max(turnover.values()) < band
      pending_status = 'PENDING_NEXT_OPEN' if event else 'HOLD_BELOW_TOP_LEVEL_3PCT_BAND' if top_level_below_band else 'PENDING_TOP_LEVEL_REVIEW'
      updated_hold=[]; updated_eq=[]; pending={'as_of':day,'status':pending_status,'earliest_trade_date':'2026-07-21','snapshot_sha256':snapshot_hash,'orders':[],'top_level_reference_turnover':turnover,'top_level_rebalance_band':band,'note':'顶层波动率每日更新；只有总换手达到3%净值或出现已审核的底层调仓/风控事件时，才生成T+1订单。'}
      account_summary={}
      for variant in ('levered','unlevered'):
        use_exp=exposure if variant=='levered' else un
        prior=[r for r in hold if r['variant']==variant]; cash=next(float(r['market_value']) for r in prior if r['code']=='CASH')
        positions={r['code']:int(float(r['shares'])) for r in prior if r['code']!='CASH'}
        value=sum(positions[c]*prices[c] for c in positions)+cash
        prior_end=float([r for r in eq if r['variant']==variant][-1]['ending_equity'])
        contrib=0.0
        cumulative=1000000.0 if kind=='fixed' else 200000.0
        pnl=value-prior_end-contrib; cpnl=value-cumulative
        gross=(value-cash)/value; risk=(value-cash-positions.get('511360',0)*prices['511360'])/value
        nav=value/cumulative
        oldmax=min(float(r.get('max_drawdown',0) or 0) for r in eq if r['variant']==variant)
        dd=nav-1; maxdd=min(oldmax,dd)
        for c in positions:
          updated_hold.append({'variant':variant,'as_of':day,'code':c,'name':NAMES[c],'shares':positions[c],'close':f'{prices[c]:.3f}','market_value':f'{positions[c]*prices[c]:.2f}','account_weight':f'{positions[c]*prices[c]/value:.8f}'})
        updated_hold.append({'variant':variant,'as_of':day,'code':'CASH','name':'现金','shares':int(cash),'close':'1.000','market_value':f'{cash:.2f}','account_weight':f'{cash/value:.8f}'})
        orders=[] if event is None else [o for o in event['orders'] if o.get('account') == kind and o.get('variant') == variant]
        for order in orders:
          if order.get('signal_date') != day or order.get('trade_date') != '2026-07-21' or order.get('code') not in NAMES:
            raise RuntimeError('approved event contains an invalid order')
        pending['orders'].extend(orders)
        base={'variant':variant,'date':day,'starting_equity':f'{prior_end:.2f}','ending_equity':f'{value:.2f}','daily_pnl':f'{pnl:.2f}','cumulative_pnl':f'{cpnl:.2f}','nav':f'{nav:.8f}','cash':f'{cash:.2f}','financing_balance':'0.00','model_exposure':f'{use_exp:.8f}','gross_etf_exposure':f'{gross:.8f}','trading_cost':'0.00','financing_cost':'0.00','status':'OK'}
        if kind=='dca': base.update({'external_contribution':'0.00','cumulative_contributions':f'{cumulative:.2f}','contribution_return':f'{value/cumulative-1:.8f}','unit_nav':f'{nav:.8f}','current_drawdown':f'{dd:.8f}','max_drawdown':f'{maxdd:.8f}','commission':'0.00','slippage':'0.00'})
        updated_eq.append(base); account_summary[variant]=(prior_end,value,pnl,cpnl,nav,dd,maxdd,cash,gross,risk,orders)
      # Stage each directory then replace only after both accounts calculate without error.
      all_results.append((directory,kind,eq,updated_eq,updated_hold,pending,latest,account_summary))
    # Validate all four before any write.
    if any(not r[7] for r in all_results): raise RuntimeError('empty account result')
    for directory,kind,old_eq,new_eq,new_hold,pending,latest,summary in all_results:
      fields=list(old_eq[0].keys()); fields += [k for k in new_eq[0] if k not in fields]
      def emit_csv(path,items,columns):
        with path.open('w',newline='',encoding='utf-8-sig') as f:
          w=csv.DictWriter(f,fieldnames=columns);w.writeheader();w.writerows(items)
      tmp=Path(tempfile.mkdtemp(dir=directory.parent))
      emit_csv(tmp/'equity.csv',old_eq+new_eq,fields)
      emit_csv(tmp/'holdings_latest.csv',new_hold,list(new_hold[0]))
      (tmp/'pending_orders.json').write_text(json.dumps(pending,ensure_ascii=False,indent=2)+'\n')
      latest['as_of']=day; latest['status']='OK'; latest['snapshot_sha256']=pending['snapshot_sha256']; latest['pending_orders_status']=pending['status']
      for v,s in summary.items():
        d=latest['variants'][v]; d.update({'ending_equity':s[1],'daily_pnl':s[2],'cumulative_pnl':s[3],'nav':s[4],'cash':s[7],'financing_balance':0.0,'model_exposure':le if v=='levered' else un,'gross_etf_exposure':s[8],'trading_cost':0.0})
        if kind=='dca': d.update({'cumulative_contributions':200000.0,'contribution_return':s[1]/200000-1,'xirr':None,'unit_nav':s[4],'current_drawdown':s[5],'max_drawdown':s[6],'commission':0.0,'slippage':0.0})
      (tmp/'account_latest.json').write_text(json.dumps(latest,ensure_ascii=False,indent=2)+'\n')
      for name in ('equity.csv','holdings_latest.csv','pending_orders.json','account_latest.json'): shutil.move(str(tmp/name),directory/name)
      tmp.rmdir()
    report=['# 三策略袖组合四账户模拟盘复盘（'+day+'）','', '> 状态：`OK`；同一行情快照重跑并通过校验。D日仅收盘估值，D日信号最早下一交易日开盘执行。','', '| 账户 | 期初资产 | 外部入金 | 期末资产 | 当日盈亏 | 累计盈亏 | 累计收益率/XIRR | 当前/最大回撤 | 现金 | 融资 | ETF毛仓位 | 风险资产仓位 | 模型暴露 |','|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    labels=[('fixed','levered','固定100万·杠杆'),('fixed','unlevered','固定100万·无杠杆'),('dca','levered','定投20万+3万·杠杆'),('dca','unlevered','定投20万+3万·无杠杆')]
    lookup={(k,v):s for _,k,_,_,_,_,_,s in all_results for v,s in s.items()}
    for kind,v,label in labels:
      s=lookup[(kind,v)]; ret=f'{s[4]-1:+.2%}' + ( ' / XIRR暂不适用' if kind=='dca' else '')
      report.append(f'| {label} | {s[0]:,.2f}元 | 0元 | {s[1]:,.2f}元 | {s[2]:+,.2f}元 | {s[3]:+,.2f}元 | {ret} | {s[5]:+.2%} / {s[6]:+.2%} | {s[7]:,.2f}元 | 0元 | {s[8]:.2%} | {s[9]:.2%} | {(le if v=="levered" else un):.4f}倍 |')
    report += ['', '## 顶层波动率执行规则', '', f'- 每日收盘测算40日组合波动率，目标年化24%，暴露平滑系数10%，下一交易日开盘才可执行。', f'- 顶层总换手达到 **{band:.0%}** 净值才调仓；本日连续权重参考换手：杠杆版 {turnover["levered"]:.2%}、无杠杆版 {turnover["unlevered"]:.2%}。', '', '## 成本拆分', '', '| 账户 | 当日佣金 | 当日滑点 | 当日融资成本 |','|---|---:|---:|---:|',*['| '+x[2]+' | 0元 | 0元 | 0元 |' for x in labels], '', '## 明日操作卡', '', '**下一交易日：2026-07-21。**', '']
    if event is None:
      reason = '本日顶层参考换手低于3%阈值。' if max(turnover.values()) < band else '本日顶层参考换手达到3%，但尚无同快照的已审核缩放订单。'
      report += [f'四账户均为：**维持持仓，无交易。** {reason} 已审核的动态袖双周调仓、严格核心月频调仓或明确记录的风控事件仍可单独产生订单。', '']
    else:
      for kind,v,label in labels:
        s=lookup[(kind,v)]; os=s[10]; commission=sum(float(o['commission']) for o in os); slippage=sum(float(o['slippage']) for o in os)
        report += [f'### {label}', '', '| 代码 | 名称 | 方向 | 目标份额 | 变动份额 | 预计金额 | 原因 |','|---|---|---|---:|---:|---:|---|']
        report += [f"| {o['code']} | {o['name']} | {o['side']} | {o.get('target_shares','—')} | {o['shares']} | {float(o['estimated_value']):,.2f}元 | {o.get('reason',event['type'])} |" for o in os] or ['| — | — | — | — | — | — | 本账户无已审核订单 |']
        report += [f'预计佣金 {commission:.2f}元、滑点 {slippage:.2f}元，合计 {commission+slippage:.2f}元。', '']
    report += ['定投账户7月不重复入金；下次外部入金为2026-08-03，每账户30,000元。定投回撤采用单位净值/TWR，XIRR待第二个现金流日期后计算。', '', f"快照SHA-256：`{sha(src/f'market_snapshot_{day}.json')}`", '', '历史回测及纸面模拟不代表未来收益，不构成投资建议。','']
    text='\n'.join(report)
    for d in (FIXED,DCA):
      (d/'daily').mkdir(exist_ok=True); (d/'daily'/f'三策略袖组合模拟盘复盘_{day}.md').write_text(text,encoding='utf-8')
    note=Path('/Users/yansenz/Documents/note'); note.mkdir(parents=True,exist_ok=True); (note/f'三策略袖组合模拟盘复盘_{day}.md').write_text(text,encoding='utf-8')
if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('day')
    parser.add_argument('--cancel-invalid-pending', action='store_true')
    args=parser.parse_args()
    cancel_invalid_pending(args.day) if args.cancel_invalid_pending else main(args.day)

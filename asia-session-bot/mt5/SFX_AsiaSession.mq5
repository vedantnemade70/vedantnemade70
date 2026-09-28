//+------------------------------------------------------------------+
//| SFX_AsiaSession.mq5                                              |
//| PO3 Asia Session strategy (ShaylinFX), port of sfx_bot/strategy.py|
//|                                                                  |
//| 1. 20:00 New York time: mark nearest 15M BSL (swing high above    |
//|    price) and SSL (swing low below price).                       |
//| 2. One of them must be purged between 20:00 and 22:00 NY.        |
//|    BSL purged -> sells, SSL purged -> buys. Otherwise no trade.   |
//| 3. On M3/M1: wait for a market structure shift, then the first   |
//|    fair value gap after it.                                      |
//| 4. Limit order at the FVG, SL at the FVG formation, TP 1:2 RR.   |
//|                                                                  |
//| Backtest: Strategy Tester, any chart timeframe, model "1 minute  |
//| OHLC" or "Every tick based on real ticks".                       |
//+------------------------------------------------------------------+
#property copyright "SFX Asia Session bot"
#property version   "1.00"
#property description "PO3 Asia Session strategy: 20:00 NY liquidity purge -> MSS -> FVG entry, 1:2 RR"

#include <Trade/Trade.mqh>

enum ENUM_SFX_MGMT  { MGMT_FIXED = 0,  // Exit at the RR target
                      MGMT_BE_RUNNER = 1 }; // At target: SL to BE, ride to opposite 15M liquidity
enum ENUM_SFX_ENTRY { ENTRY_EDGE = 0,  // First touch of the FVG
                      ENTRY_MID = 1 }; // 50% of the FVG
enum ENUM_SFX_SL    { SL_FVG = 0,      // High/low of the 3 FVG candles
                      SL_SWING = 1 };  // Purge extreme

input group "Session (New York time)"
input int    InpServerMinusNY    = 7;     // Server time minus NY time, hours (GMT+2/+3 brokers: 7)
input int    InpAnchorHour       = 20;    // Mark liquidity at (hour, NY)
input int    InpPurgeEndHour     = 22;    // Purge must happen before (hour, NY)
input double InpEntryCutoffHours = 4.0;   // Entry allowed until anchor + hours
input double InpMaxTradeHours    = 11.0;  // Close any trade at anchor + hours

input group "Structure"
input ENUM_TIMEFRAMES InpHTF     = PERIOD_M15; // Liquidity timeframe
input int    InpHTFSwing         = 2;     // HTF swing strength (bars each side)
input int    InpHTFLookback      = 96;    // HTF bars searched before the anchor
input ENUM_TIMEFRAMES InpLTF     = PERIOD_M3;  // MSS/FVG timeframe (M1 or M3)
input int    InpLTFSwing         = 1;     // LTF swing strength
input double InpMinFVGPoints     = 0;     // Minimum FVG size, points

input group "Trade"
input ENUM_SFX_ENTRY InpEntryMode = ENTRY_EDGE;
input ENUM_SFX_SL    InpSLMode    = SL_FVG;
input double InpSLBufferPoints   = 0;     // Extra SL distance, points
input double InpRR               = 2.0;   // Reward : risk
input ENUM_SFX_MGMT  InpMgmt      = MGMT_FIXED;
input bool   InpCancelIfTargetFirst = true; // Cancel order if target prints before fill
input double InpRiskPercent      = 1.0;   // Risk per trade, % of balance
input double InpFixedLots        = 0;     // Fixed lots (0 = use risk %)
input long   InpMagic            = 20002200;

enum ENUM_SFX_STATE { ST_IDLE, ST_WAIT_PURGE, ST_WAIT_SETUP, ST_PENDING, ST_IN_TRADE, ST_DONE };

CTrade   trade;
ENUM_SFX_STATE g_state = ST_IDLE;
datetime g_anchor = 0, g_windowEnd = 0, g_cutoff = 0, g_closeBy = 0;
datetime g_lastBar = 0, g_purgeTime = 0;
bool     g_hasBsl = false, g_hasSsl = false, g_atBE = false;
double   g_bsl = 0, g_ssl = 0;
int      g_dir = 0;                      // -1 sell, +1 buy
double   g_entry = 0, g_sl = 0, g_tp = 0, g_runner = 0;
ulong    g_order = 0;

//+------------------------------------------------------------------+
int OnInit()
{
   if(InpLTF != PERIOD_M1 && InpLTF != PERIOD_M3)
      Print("Note: the strategy is designed for M1/M3 as the entry timeframe.");
   trade.SetExpertMagicNumber(InpMagic);
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
void OnTick()
{
   datetime bar = iTime(_Symbol, PERIOD_M1, 0);
   if(bar != g_lastBar && bar != 0)
   {
      g_lastBar = bar;
      OnNewMinute(bar);
   }
   if(g_state == ST_IN_TRADE)
      ManagePosition(TimeCurrent());
}

//+------------------------------------------------------------------+
//| Session anchor (server time) for the session active at `now`.    |
//+------------------------------------------------------------------+
datetime SessionAnchor(datetime now)
{
   long off = (long)InpServerMinusNY * 3600;
   datetime ny = (datetime)(now - off);
   MqlDateTime d;
   TimeToStruct(ny, d);
   d.hour = InpAnchorHour; d.min = 0; d.sec = 0;
   datetime a = StructToTime(d);
   if(ny < a) a -= 86400;
   return (datetime)(a + off);
}

//+------------------------------------------------------------------+
void OnNewMinute(datetime now)
{
   datetime anchor = SessionAnchor(now);
   if(anchor != g_anchor)
      StartSession(anchor, now);

   switch(g_state)
   {
      case ST_WAIT_PURGE: CheckPurge(now); break;
      case ST_WAIT_SETUP: TryBuildSetup(now); break;
      case ST_PENDING:    CheckPending(now); break;
      case ST_IN_TRADE:   ManagePosition(now); break;
      default: break;
   }
}

//+------------------------------------------------------------------+
void StartSession(datetime anchor, datetime now)
{
   // Leftover pending order from the previous session.
   if(g_state == ST_PENDING && OrderSelect(g_order))
      trade.OrderDelete(g_order);

   g_anchor    = anchor;
   g_windowEnd = anchor + (InpPurgeEndHour - InpAnchorHour) * 3600;
   g_cutoff    = anchor + (int)(InpEntryCutoffHours * 3600);
   g_closeBy   = anchor + (int)(InpMaxTradeHours * 3600);
   g_dir = 0; g_order = 0; g_atBE = false;
   g_hasBsl = g_hasSsl = false;
   g_state = ST_DONE;

   if(now >= g_windowEnd || HasMyPosition())
      return;
   if(!MarkLiquidity())
   {
      PrintFormat("%s no 15M liquidity", TimeToString(anchor));
      return;
   }
   g_state = ST_WAIT_PURGE;
   PrintFormat("%s session: BSL %s SSL %s", TimeToString(anchor),
               g_hasBsl ? DoubleToString(g_bsl, _Digits) : "-",
               g_hasSsl ? DoubleToString(g_ssl, _Digits) : "-");
}

//+------------------------------------------------------------------+
//| STEP 1+2: nearest untouched HTF swing high above / low below.    |
//+------------------------------------------------------------------+
bool MarkLiquidity()
{
   MqlRates r[];
   int n = CopyRates(_Symbol, InpHTF, (datetime)(g_anchor - PeriodSeconds(InpHTF)), InpHTFLookback, r);
   int k = InpHTFSwing;
   if(n < 2 * k + 2) return false;
   double ref = r[n - 1].close;

   for(int i = k; i <= n - 1 - k; i++)
   {
      bool sh = true, sl = true;
      for(int j = 1; j <= k; j++)
      {
         if(r[i - j].high >= r[i].high || r[i + j].high > r[i].high) sh = false;
         if(r[i - j].low  <= r[i].low  || r[i + j].low  < r[i].low)  sl = false;
      }
      bool untouchedH = true, untouchedL = true;
      for(int j = i + 1; j < n; j++)
      {
         if(r[j].high >= r[i].high) untouchedH = false;
         if(r[j].low  <= r[i].low)  untouchedL = false;
      }
      if(sh && untouchedH && r[i].high > ref && (!g_hasBsl || r[i].high < g_bsl))
      { g_bsl = r[i].high; g_hasBsl = true; }
      if(sl && untouchedL && r[i].low < ref && (!g_hasSsl || r[i].low > g_ssl))
      { g_ssl = r[i].low; g_hasSsl = true; }
   }
   return g_hasBsl || g_hasSsl;
}

//+------------------------------------------------------------------+
//| STEP 3: first purge between 20:00 and 22:00 NY.                  |
//+------------------------------------------------------------------+
void CheckPurge(datetime now)
{
   datetime t = iTime(_Symbol, PERIOD_M1, 1);
   if(t >= g_windowEnd || now > g_windowEnd + 60)
   {
      g_state = ST_DONE;
      PrintFormat("%s invalid: no purge before %02d:00 NY", TimeToString(g_anchor), InpPurgeEndHour);
      return;
   }
   if(t < g_anchor) return;
   bool hitB = g_hasBsl && iHigh(_Symbol, PERIOD_M1, 1) > g_bsl;
   bool hitS = g_hasSsl && iLow(_Symbol, PERIOD_M1, 1) < g_ssl;
   if(!hitB && !hitS) return;
   if(hitB && hitS)
   {
      g_state = ST_DONE;
      Print("invalid: BSL and SSL purged in the same minute");
      return;
   }
   g_dir = hitB ? -1 : 1;
   g_purgeTime = t;
   g_state = ST_WAIT_SETUP;
   PrintFormat("%s purge of %s -> looking for %s", TimeToString(t), hitB ? "BSL" : "SSL", hitB ? "sells" : "buys");
   TryBuildSetup(now);
}

//+------------------------------------------------------------------+
bool IsSwingLow(const double &l[], int j, int k)
{
   for(int m = 1; m <= k; m++)
      if(l[j - m] <= l[j] || l[j + m] < l[j]) return false;
   return true;
}

//+------------------------------------------------------------------+
//| STEP 3+4 on the LTF: MSS -> FVG -> order levels.                 |
//| Prices are mirrored for buys so one sell routine covers both.    |
//| Returns 1 ready, 0 keep waiting, -1 setup failed.                |
//+------------------------------------------------------------------+
int ScanSetup(datetime now)
{
   int step = PeriodSeconds(InpLTF);
   MqlRates r[];
   int got = CopyRates(_Symbol, InpLTF, (datetime)(g_anchor - 3 * 3600), (datetime)(now - 1), r);
   if(got <= 0) return 0;
   int n = got;
   while(n > 0 && r[n - 1].time + step > now) n--;   // closed bars only
   if(n < 3) return 0;

   double h[], l[], c[];
   ArrayResize(h, n); ArrayResize(l, n); ArrayResize(c, n);
   for(int i = 0; i < n; i++)
   {
      if(g_dir < 0) { h[i] = r[i].high; l[i] = r[i].low; c[i] = r[i].close; }
      else          { h[i] = -r[i].low; l[i] = -r[i].high; c[i] = -r[i].close; }
   }

   datetime purgeBar = (datetime)((long)g_purgeTime - (long)g_purgeTime % step);
   int start = -1;
   for(int i = 0; i < n; i++) if(r[i].time >= purgeBar) { start = i; break; }
   if(start < 0) return 0;

   int k = InpLTFSwing, ext = start, mss = -1;
   for(int i = start; i < n; i++)
   {
      if(r[i].time >= g_cutoff) return -1;
      if(h[i] >= h[ext]) ext = i;
      int j = MathMin(ext - 1, i - k - 1);
      for(; j >= k; j--)
         if(IsSwingLow(l, j, k)) break;
      if(j >= k && c[i] < l[j]) { mss = i; break; }
   }
   if(mss < 0) return 0;
   double extreme = h[ext];

   int fvg = -1;
   double minGap = InpMinFVGPoints * _Point;
   for(int i = mss + 1; i < n; i++)
   {
      if(r[i].time >= g_cutoff) return -1;
      if(h[i] > extreme) return -1;
      double gap = l[i - 2] - h[i];
      if(gap > 0 && gap >= minGap) { fvg = i; break; }
   }
   if(fvg < 0) return 0;

   double fvgLow = h[fvg], fvgHigh = l[fvg - 2];
   double entry = (InpEntryMode == ENTRY_EDGE) ? fvgLow : (fvgLow + fvgHigh) / 2.0;
   double base = (InpSLMode == SL_FVG) ? MathMax(h[fvg], MathMax(h[fvg - 1], h[fvg - 2])) : extreme;
   double sl = base + InpSLBufferPoints * _Point;
   double risk = sl - entry;
   if(risk <= 0) return -1;
   double tp = entry - InpRR * risk;
   double runner = tp;
   bool hasOpp = (g_dir < 0) ? g_hasSsl : g_hasBsl;
   double opp = (g_dir < 0) ? g_ssl : -g_bsl;
   if(InpMgmt == MGMT_BE_RUNNER && hasOpp && opp < tp) runner = opp;

   double s = (g_dir < 0) ? 1.0 : -1.0;   // undo the mirror for buys
   g_entry = s * entry; g_sl = s * sl; g_tp = s * tp; g_runner = s * runner;
   return 1;
}

//+------------------------------------------------------------------+
void TryBuildSetup(datetime now)
{
   if(now >= g_cutoff) { g_state = ST_DONE; Print("expired: no setup before the cutoff"); return; }
   int res = ScanSetup(now);
   if(res < 0) { g_state = ST_DONE; Print("setup cancelled"); return; }
   if(res == 0) return;
   PlaceOrder();
}

//+------------------------------------------------------------------+
double LotsForRisk()
{
   if(InpFixedLots > 0) return InpFixedLots;
   double tickSize = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE_LOSS);
   if(tickValue <= 0) tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double lossPerLot = MathAbs(g_entry - g_sl) / tickSize * tickValue;
   if(lossPerLot <= 0) return 0;
   double lots = AccountInfoDouble(ACCOUNT_BALANCE) * InpRiskPercent / 100.0 / lossPerLot;
   lots = MathFloor(lots / step) * step;
   lots = MathMax(lots, SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN));
   lots = MathMin(lots, SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX));
   return NormalizeDouble(lots, 2);
}

//+------------------------------------------------------------------+
void PlaceOrder()
{
   double lots = LotsForRisk();
   double entry = NormalizeDouble(g_entry, _Digits);
   double sl = NormalizeDouble(g_sl, _Digits);
   double tp = NormalizeDouble(InpMgmt == MGMT_BE_RUNNER ? g_runner : g_tp, _Digits);
   string cmt = "SFX Asia";
   bool ok;
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID), ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);

   if(g_dir < 0)
   {
      if(bid >= entry) ok = trade.Sell(lots, _Symbol, 0, sl, tp, cmt);  // already in the FVG
      else             ok = trade.SellLimit(lots, entry, _Symbol, sl, tp, ORDER_TIME_GTC, 0, cmt);
   }
   else
   {
      if(ask <= entry) ok = trade.Buy(lots, _Symbol, 0, sl, tp, cmt);
      else             ok = trade.BuyLimit(lots, entry, _Symbol, sl, tp, ORDER_TIME_GTC, 0, cmt);
   }
   if(!ok)
   {
      PrintFormat("order failed: %d %s", trade.ResultRetcode(), trade.ResultRetcodeDescription());
      g_state = ST_DONE;
      return;
   }
   g_order = trade.ResultOrder();
   g_state = HasMyPosition() ? ST_IN_TRADE : ST_PENDING;
   PrintFormat("%s %s entry %.*f SL %.*f TP %.*f lots %.2f", g_dir < 0 ? "SELL" : "BUY",
               g_state == ST_IN_TRADE ? "market" : "limit",
               _Digits, entry, _Digits, sl, _Digits, tp, lots);
}

//+------------------------------------------------------------------+
void CheckPending(datetime now)
{
   if(!OrderSelect(g_order))
   {
      // Filled (or filled and closed within the minute).
      g_state = HasMyPosition() ? ST_IN_TRADE : ST_DONE;
      return;
   }
   if(now >= g_cutoff)
   {
      trade.OrderDelete(g_order);
      g_state = ST_DONE;
      Print("pending order expired at the cutoff");
      return;
   }
   if(InpCancelIfTargetFirst)
   {
      bool hit = (g_dir < 0) ? iLow(_Symbol, PERIOD_M1, 1) <= g_tp : iHigh(_Symbol, PERIOD_M1, 1) >= g_tp;
      if(hit)
      {
         trade.OrderDelete(g_order);
         g_state = ST_DONE;
         Print("target reached before mitigation, order cancelled");
      }
   }
}

//+------------------------------------------------------------------+
bool SelectMyPosition()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong t = PositionGetTicket(i);
      if(t > 0 && PositionGetString(POSITION_SYMBOL) == _Symbol && PositionGetInteger(POSITION_MAGIC) == InpMagic)
         return true;
   }
   return false;
}

bool HasMyPosition() { return SelectMyPosition(); }

//+------------------------------------------------------------------+
void ManagePosition(datetime now)
{
   if(!SelectMyPosition()) { g_state = ST_DONE; return; }
   ulong ticket = (ulong)PositionGetInteger(POSITION_TICKET);
   if(now >= g_closeBy)
   {
      trade.PositionClose(ticket);
      g_state = ST_DONE;
      Print("time exit");
      return;
   }
   if(InpMgmt == MGMT_BE_RUNNER && !g_atBE && g_runner != g_tp)
   {
      double px = (g_dir < 0) ? SymbolInfoDouble(_Symbol, SYMBOL_ASK) : SymbolInfoDouble(_Symbol, SYMBOL_BID);
      if((px - g_tp) * g_dir >= 0)
      {
         if(trade.PositionModify(ticket, NormalizeDouble(g_entry, _Digits), NormalizeDouble(g_runner, _Digits)))
         {
            g_atBE = true;
            Print("target reached: SL moved to break even, riding to 15M liquidity");
         }
      }
   }
}
//+------------------------------------------------------------------+

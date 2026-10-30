市场状态：{{regime}}
我的持仓（仅名称）：{{holdings}}

最近 7 天新闻标题：
{{news}}

行业板块 5 日涨跌前列：{{boards}}
行业资金流前列：{{flows}}

可关联标的（只能从中选择）：
{{universe}}

请输出 JSON：
{"market_summary":"不超过80字","topics":[{"name":"热点名","summary":"不超过60字","direction":"bullish|bearish|neutral","confidence":0.7,"related_symbols":[{"code":"510300","name":"沪深300ETF","relation":"direct"}],"evidence":["新闻标题原文片段"],"risk":"不超过40字"}],"disclaimer":"仅供参考，不构成投资建议"}

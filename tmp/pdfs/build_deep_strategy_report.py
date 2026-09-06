from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether

OUT = "/Users/khand/Git/pmcc_backtest_ruby/output/pdf/deep_research_strategy_benchmark.pdf"

NAVY = colors.HexColor("#14213D")
BLUE = colors.HexColor("#2F66B0")
PALE = colors.HexColor("#EDF3FA")
GREEN = colors.HexColor("#DDEFE3")
AMBER = colors.HexColor("#FFF1CC")
RED = colors.HexColor("#F9DEDE")
INK = colors.HexColor("#202936")
MUTED = colors.HexColor("#5D6877")

styles = getSampleStyleSheet()
styles.add(ParagraphStyle(name="TitleX", parent=styles["Title"], fontName="Helvetica-Bold", fontSize=22, leading=26, textColor=NAVY, spaceAfter=10))
styles.add(ParagraphStyle(name="Sub", parent=styles["Normal"], fontSize=9, leading=13, textColor=MUTED, spaceAfter=14))
styles.add(ParagraphStyle(name="H1X", parent=styles["Heading1"], fontName="Helvetica-Bold", fontSize=15, leading=18, textColor=NAVY, spaceBefore=10, spaceAfter=7))
styles.add(ParagraphStyle(name="H2X", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=11.5, leading=14, textColor=BLUE, spaceBefore=8, spaceAfter=5))
styles.add(ParagraphStyle(name="BodyX", parent=styles["BodyText"], fontSize=9.3, leading=13.2, textColor=INK, spaceAfter=7))
styles.add(ParagraphStyle(name="Small", parent=styles["BodyText"], fontSize=7.4, leading=10, textColor=MUTED, spaceAfter=3))
styles.add(ParagraphStyle(name="Callout", parent=styles["BodyText"], fontName="Helvetica-Bold", fontSize=11, leading=15, textColor=NAVY, leftIndent=10, rightIndent=10, spaceBefore=8, spaceAfter=8))

def P(text, style="BodyX"):
    return Paragraph(text, styles[style])

def footer(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(colors.HexColor("#D6DEE8"))
    canvas.line(0.62*inch, 0.48*inch, 7.88*inch, 0.48*inch)
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(0.65*inch, 0.3*inch, "Deep research | 5 September 2026")
    canvas.drawRightString(7.85*inch, 0.3*inch, f"Page {doc.page}")
    canvas.restoreState()

doc = SimpleDocTemplate(OUT, pagesize=letter, rightMargin=0.62*inch, leftMargin=0.62*inch, topMargin=0.58*inch, bottomMargin=0.62*inch, title="Deep Research: Strategies versus 67.8% CAGR / 35% Drawdown")
story = []
story += [P("Can any public strategy beat 67.8% CAGR with 35% drawdown?", "TitleX"), P("Evidence review of reproducible trading strategies, public backtests, and high-return claims", "Sub")]

callout = Table([[P("DIRECT ANSWER", "Small"), P("No publicly reproducible strategy was found that credibly beats both thresholds. The few claims that do are opaque, anonymous, or unvalidated.", "Callout")]], colWidths=[1.0*inch, 6.05*inch])
callout.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),PALE),("BOX",(0,0),(-1,-1),0.8,BLUE),("VALIGN",(0,0),(-1,-1),"MIDDLE"),("LEFTPADDING",(0,0),(-1,-1),8),("RIGHTPADDING",(0,0),(-1,-1),8),("TOPPADDING",(0,0),(-1,-1),7),("BOTTOMPADDING",(0,0),(-1,-1),7)]))
story += [callout, Spacer(1,8), P("Scope and standard", "H1X"), P("The screen required CAGR above 67.8% <b>and</b> maximum drawdown no worse than 35%. Preference went to liquid U.S. instruments, multi-year samples comparable with 2016-2026, exact executable rules, realistic costs, and evidence that does not depend on trusting the author. Reported numbers were not treated as verified merely because they appeared in a PDF or platform result.")]

rows = [
    ["Candidate", "CAGR", "Max DD", "Assessment"],
    ["Current laggard-7 / GLD-only QC", "67.805%", "35.1%", "Benchmark; reproducible, optimized in-sample"],
    ["SMA Trading anonymous PDF", "173.22%", "21.64%", "Fails verification: no rules, assets, code, or OOS"],
    ["Anonymous TQQQ rotation", "72%", "32%", "Fails verification: vague rules, synthetic data"],
    ["QC modified In & Out", "73.358%", "~54%", "Fails drawdown; modified code withheld"],
    ["QC RSI Rebalance", "66.214%", "32.3%", "Best open-code near-miss"],
    ["QC intraday volatility harvester", "53.415%", "20.7%", "Open code; fails return threshold"],
    ["Triple X semiconductor timing", "59.54%", "n/c", "Fails return threshold; commercial retrospective"],
    ["TQQQ/SOXL macro rotation", "49.9%", "41.4%", "Fails both; 54,000-combination search"],
    ["50/50 TQQQ/TMF bimonthly", "44.9%", "<25% EOM", "Fails return; drawdown not daily"],
]
t = Table([[P(str(c), "Small") for c in r] for r in rows], colWidths=[2.25*inch,.72*inch,.72*inch,3.25*inch], repeatRows=1)
t.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),NAVY),("TEXTCOLOR",(0,0),(-1,0),colors.white),("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),("GRID",(0,0),(-1,-1),0.35,colors.HexColor("#CDD6E2")),("VALIGN",(0,0),(-1,-1),"TOP"),("LEFTPADDING",(0,0),(-1,-1),5),("RIGHTPADDING",(0,0),(-1,-1),5),("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5),("BACKGROUND",(0,1),(-1,1),GREEN),("BACKGROUND",(0,2),(-1,4),RED),("BACKGROUND",(0,5),(-1,6),AMBER)]))
story += [P("Candidate screen", "H1X"), t]

story += [PageBreak(), P("Why the apparent winners do not qualify", "H1X"),
P("SMA Trading: 173.22% CAGR / 21.64% drawdown", "H2X"), P("The indexed PDF covers 30 August 2017 through 11 March 2025 and reports 55% time in market, a 635% best year, no losing calendar year, and a 2.24 Sharpe ratio. The public artifact does not identify the traded instruments, complete rules, code, costs, or an untouched test period. The performance claim exists; a reproducible strategy does not."),
P("Anonymous TQQQ rotation: 72% / 32%", "H2X"), P("The described universe is TQQQ, QID, UGL, and SGOV, with moving averages, disparity, VIX thresholds, and RSI. Exact thresholds and sizing are withheld. Pre-2010 TQQQ is synthetic, the rules were finalized from the same backtest, and the author acknowledges that the live record is too short for a meaningful CAGR. Community reviewers flag implausible annual patterns."),
P("QuantConnect In & Out: 73.358% / about 54%", "H2X"), P("This is the closest high-return platform result. The author explicitly withholds the modified code. In response to a request for drawdown, the author reports about 0.54, which fails the ceiling. The discussion also records that alternate price sources changed one result from roughly 1,700% to 400%."),
P("Best reproducible near-miss", "H1X"), P("The open QuantConnect <b>RSI Rebalance</b> example reports 66.214% CAGR, 32.3% drawdown, 1.773 Sharpe, 1,845 orders, $133,085 in fees on $10,000 starting capital, and 21.38% turnover. It trades TQQQ, QQQ, UVXY, XLU, and GLD with daily RSI-based allocation. It misses the return threshold by 1.59 percentage points and is operationally much heavier than monthly rotation, but its public code makes it the only candidate close enough to justify independent reproduction."),
P("A lower-drawdown research lead", "H2X"), P("The open QuantConnect Intraday Volatility Regime Harvester reports 53.415% CAGR and 20.7% drawdown after $1.46 million in fees on $1 million initial capital. It trades UVXY/TQQQ at seven intraday times with highly specific thresholds. It fails the return threshold and cannot be managed in 15-30 minutes per week."),
P("What the threshold implies", "H1X"), P("A 67.8% CAGR with 35% drawdown implies a Calmar ratio near <b>1.94</b> for roughly a decade. That is an exceptionally high hurdle for a liquid, scalable public strategy. The current result is itself selected from multiple risk-selection rules, laggard windows, stop percentages, and defensive universes, so 67.8% should be treated as an optimized historical outcome rather than a forward expectation.")]

story += [PageBreak(), P("Evidence interpretation", "H1X"),
P("Selection bias is the central issue", "H2X"), P("Bailey, Borwein, Lopez de Prado, and Zhu define backtest overfitting as selecting a configuration that is optimal in-sample but ranks below the median out-of-sample. Their Probability of Backtest Overfitting framework measures that risk. Bailey and Lopez de Prado's Deflated Sharpe Ratio additionally adjusts for multiple testing, non-normal returns, sample length, and the distribution of attempted trials. Ordinary CAGR and Sharpe do not contain this penalty."),
P("Leverage adds path dependence", "H2X"), P("The SEC states that leveraged ETFs generally target a multiple of <i>daily</i> returns. Longer-period performance can differ significantly from that multiple, especially in volatile markets, and investors may experience sudden losses. Using actual ETF histories captures realized daily reset effects, but optimizing a timing rule on one realized path can still select favorable noise."),
P("Recommended decision", "H1X"),
P("Do not replace the current strategy with any discovered claim. Instead:")]
bullets = [
"Freeze the rule set: TQQQ/TECL, seven-session laggard, GLD-only defense, and a 10.5% monthly-reset stop.",
"Reserve data after September 2026 as a truly untouched forward test; record every scheduled signal, order, and executable fill.",
"Reconstruct the full family of parameters already tested and calculate PBO/Deflated Sharpe rather than judging the winner's Sharpe alone.",
"Reproduce the open QC RSI Rebalance strategy over exactly 2016-2026 with identical costs and next-bar execution; it is the only credible near-miss.",
"Reject future candidates lacking exact rules, costs, survivorship-safe data, next-bar fills, and an untouched evaluation period."
]
for b in bullets: story.append(Paragraph("• " + b, ParagraphStyle(name="bx"+str(len(story)), parent=styles["BodyX"], leftIndent=13, firstLineIndent=-8, spaceAfter=5)))

story += [P("Source notes", "H1X")]
sources = [
("QuantConnect - RSI Rebalance embedded backtest", "https://www.quantconnect.com/terminal/cache/embedded_backtest_83fec99a44c2f2d3bea9fa3dd7027854.html"),
("QuantConnect - Intraday Volatility Regime Harvester", "https://www.quantconnect.com/terminal/cache/embedded_backtest_562c7d4edcab5662cbd37ffc8ac9440b.html"),
("QuantConnect - In & Out Strategy discussion, page 8", "https://www.quantconnect.com/forum/discussion/9597/the-in-amp-out-strategy-continued-from-quantopian/p8"),
("SMA Trading - anonymous backtest PDF", "https://smatrading.com/assets/pdf/backtest.pdf"),
("Reddit r/TQQQ - claimed 72% CAGR strategy", "https://www.reddit.com/r/TQQQ/comments/1qdx3tk/i_built_a_strategy_to_survive_the_dotcom_crash/"),
("Triple X Market Timing - Semiconductor Strategy", "https://fact.3xtiming.com/pdf/fact_xsc.pdf"),
("Reddit r/LETFs - 54,000-test TQQQ/SOXL rotation", "https://www.reddit.com/r/LETFs/comments/1tnwufk/i_ran_54000_backtests_on_a_tqqqsoxl_rotation/"),
("QuantConnect - TQQQ/TMF study discussion", "https://www.quantconnect.com/forum/discussion/9632/amazing-returns-superior-stock-selection-strategy-superior-in-amp-out-strategy/p12"),
("Bailey et al. - The Probability of Backtest Overfitting", "https://carmamaths.org/jon/backtest2.pdf"),
("Bailey and Lopez de Prado - The Deflated Sharpe Ratio", "https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf"),
("U.S. SEC - Leveraged and Inverse ETFs bulletin", "https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-alerts/sec"),
]
for i,(name,url) in enumerate(sources,1): story.append(P(f'{i}. <link href="{url}" color="#2F66B0">{name}</link>', "Small"))
story += [Spacer(1,6), P("Research limitations", "H2X"), P("The search covered public web pages indexed as of 5 September 2026. Private funds, paywalled rule sets, unindexed code, and unverifiable brokerage records were excluded. A failure to find a credible superior strategy is not proof none exists. It means no candidate met the stated evidence standard.", "Small")]

doc.build(story, onFirstPage=footer, onLaterPages=footer)
print(OUT)

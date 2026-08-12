#!/usr/bin/env python3
"""
SENDERSMS Complete User Guide - Restaurant Edition
Generates PDF with reportlab, using Nigerian restaurant brands as examples.
"""
import os
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm, cm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.colors import HexColor, white, black
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY, TA_RIGHT
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle,
                                PageBreak, ListFlowable, ListItem, HRFlowable, KeepTogether, NextPageTemplate)
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from datetime import datetime
import textwrap

# Colors - WhatsApp / Restaurant branding
COLORS = {
    "primary": HexColor("#00a884"),  # WhatsApp green
    "primary_dark": HexColor("#008069"),
    "primary_light": HexColor("#d9fdd3"),
    "secondary": HexColor("#ffad1f"), # warm orange for restaurants
    "dark": HexColor("#111b21"),
    "dark2": HexColor("#202c33"),
    "gray": HexColor("#54656f"),
    "gray_light": HexColor("#f0f2f5"),
    "gray_border": HexColor("#e9edef"),
    "accent": HexColor("#075e54"),
    "red": HexColor("#ea4335"),
    "blue": HexColor("#34b7f1"),
}

W, H = A4

def header_footer(canvas, doc):
    canvas.saveState()
    # header line
    if doc.page > 1:
        canvas.setStrokeColor(COLORS["gray_light"])
        canvas.setLineWidth(0.5)
        canvas.line(20*mm, H - 15*mm, W - 20*mm, H - 15*mm)
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(COLORS["gray"])
        canvas.drawString(20*mm, H - 12*mm, "SENDERSMS  •  Restaurant Bulk SMS Platform")
        canvas.drawRightString(W - 20*mm, H - 12*mm, f"Page {doc.page}")
        # footer
        canvas.setStrokeColor(COLORS["gray_light"])
        canvas.line(20*mm, 15*mm, W - 20*mm, 15*mm)
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(COLORS["gray"])
        canvas.drawString(20*mm, 12*mm, "© 2026 SENDERSMS • Confidential • For restaurant partners only")
        canvas.drawRightString(W - 20*mm, 12*mm, "Need help? In-app → Settings → Support")
    canvas.restoreState()

def cover_header_footer(canvas, doc):
    # No header/footer on cover
    pass

styles = getSampleStyleSheet()

# Custom styles
s_title = ParagraphStyle('CoverTitle', parent=styles['Title'], fontSize=34, leading=38, textColor=white, alignment=TA_CENTER, fontName='Helvetica-Bold', spaceAfter=6)
s_subtitle = ParagraphStyle('CoverSub', parent=styles['Normal'], fontSize=13, leading=18, textColor=white, alignment=TA_CENTER, fontName='Helvetica', spaceAfter=12)
s_h1 = ParagraphStyle('H1', parent=styles['Heading1'], fontSize=22, leading=26, textColor=COLORS["primary_dark"], fontName='Helvetica-Bold', spaceBefore=18, spaceAfter=8, keepWithNext=True, borderPadding=(0,0,6,0))
s_h2 = ParagraphStyle('H2', parent=styles['Heading2'], fontSize=14, leading=18, textColor=COLORS["accent"], fontName='Helvetica-Bold', spaceBefore=14, spaceAfter=6, keepWithNext=True)
s_h3 = ParagraphStyle('H3', parent=styles['Heading3'], fontSize=11, leading=14, textColor=COLORS["dark"], fontName='Helvetica-Bold', spaceBefore=10, spaceAfter=4)
s_body = ParagraphStyle('Body', parent=styles['Normal'], fontSize=9.5, leading=14.5, textColor=COLORS["dark"], fontName='Helvetica', alignment=TA_JUSTIFY, spaceAfter=6)
s_bullet = ParagraphStyle('Bullet', parent=s_body, leftIndent=14, bulletIndent=6, spaceAfter=3)
s_small = ParagraphStyle('Small', parent=styles['Normal'], fontSize=8, leading=11, textColor=COLORS["gray"], fontName='Helvetica', alignment=TA_LEFT, spaceAfter=3)
s_caption = ParagraphStyle('Caption', parent=styles['Normal'], fontSize=7.5, leading=10, textColor=COLORS["gray"], fontName='Helvetica-Oblique', alignment=TA_CENTER, spaceBefore=4, spaceAfter=10)
s_table_header = ParagraphStyle('TableHeader', parent=styles['Normal'], fontSize=7.5, leading=9, textColor=white, fontName='Helvetica-Bold', alignment=TA_CENTER)
s_table_cell = ParagraphStyle('TableCell', parent=styles['Normal'], fontSize=7.5, leading=9, textColor=COLORS["dark"], fontName='Helvetica', alignment=TA_LEFT)
s_table_cell_center = ParagraphStyle('TableCellCenter', parent=s_table_cell, alignment=TA_CENTER)
s_kbd = ParagraphStyle('Kbd', parent=styles['Normal'], fontSize=8, leading=10, textColor=COLORS["primary_dark"], fontName='Helvetica-Bold', backColor=COLORS["primary_light"], borderPadding=(2,4,2,4))
s_quote = ParagraphStyle('Quote', parent=styles['Normal'], fontSize=9, leading=13, textColor=COLORS["primary_dark"], fontName='Helvetica-Oblique', leftIndent=12, borderPadding=(6,6,6,12), backColor=HexColor("#f0f9f6"), borderColor=COLORS["primary"], borderWidth=0, spaceAfter=8)

def p(text, style=s_body):
    return Paragraph(text, style)

def bullet(text):
    return Paragraph(text, s_bullet, bulletText='•')

def code_block(text):
    style = ParagraphStyle('Code', parent=styles['Code'], fontSize=7, leading=9, textColor=COLORS["dark2"], fontName='Helvetica', backColor=HexColor("#f6f8fa"), borderPadding=(6,6,6,6), spaceAfter=8, alignment=TA_LEFT)
    # escape
    esc = text.replace('&','&amp;').replace('<','&lt;').replace('>','&gt;').replace('\n','<br/>')
    return Paragraph(f"<font face='Courier' size='7'>{esc}</font>", style)

def make_table(data, col_widths, header=True):
    tbl_style = TableStyle([
        ('BACKGROUND', (0,0), (-1,0), COLORS["primary_dark"]),
        ('TEXTCOLOR', (0,0), (-1,0), white),
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,0), 7.5),
        ('BOTTOMPADDING', (0,0), (-1,0), 6),
        ('TOPPADDING', (0,0), (-1,0), 6),
        ('BACKGROUND', (0,1), (-1,-1), white),
        ('TEXTCOLOR', (0,1), (-1,-1), COLORS["dark"]),
        ('FONTNAME', (0,1), (-1,-1), 'Helvetica'),
        ('FONTSIZE', (0,1), (-1,-1), 7.5),
        ('GRID', (0,0), (-1,-1), 0.5, COLORS["gray_border"]),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [white, COLORS["gray_light"]]),
        ('LEFTPADDING', (0,0), (-1,-1), 6),
        ('RIGHTPADDING', (0,0), (-1,-1), 6),
        ('TOPPADDING', (0,1), (-1,-1), 5),
        ('BOTTOMPADDING', (0,1), (-1,-1), 5),
    ])
    # wrap cells as Paragraphs if strings
    wrapped = []
    for r_idx, row in enumerate(data):
        nr = []
        for c in row:
            if isinstance(c, str):
                style = s_table_header if r_idx==0 and header else s_table_cell
                nr.append(Paragraph(c, style))
            else:
                nr.append(c)
        wrapped.append(nr)
    t = Table(wrapped, colWidths=col_widths, repeatRows=1 if header else 0)
    t.setStyle(tbl_style)
    return t

def image_or_placeholder(path, width=170*mm, height=70*mm, caption=""):
    if os.path.exists(path):
        try:
            im = Image(path, width=width, height=height, kind='proportional')
            # preserve aspect
            im.hAlign = 'CENTER'
            if caption:
                return [im, p(f"<i>{caption}</i>", s_caption)]
            return [im]
        except Exception as e:
            print(f"Image load failed {path}: {e}")
    # placeholder box
    from reportlab.graphics.shapes import Drawing, Rect, String
    d = Drawing(width, height)
    d.add(Rect(0,0,width,height, fillColor=COLORS["gray_light"], strokeColor=COLORS["gray_border"]))
    d.add(String(width/2, height/2, f"[ Screenshot: {caption or os.path.basename(path)} ]", textAnchor="middle", fontSize=8, fillColor=COLORS["gray"]))
    if caption:
        return [d, p(f"<i>{caption}</i>", s_caption)]
    return [d]

def build_pdf(output_path):
    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        leftMargin=20*mm, rightMargin=20*mm,
        topMargin=18*mm, bottomMargin=18*mm,
        title="SENDERSMS Restaurant User Guide",
        author="SENDERSMS",
        subject="Complete guide for restaurant SMS campaigns",
        keywords="SMS, restaurant, marketing, Chicken Republic, Domino's",
    )
    story = []
    # COVER
    # Full bleed cover simulation via first page with colored background via canvas? We'll do story cover with large image and overlaid text using Table
    cover_img_path = "docs/images/cover_restaurants.jpg"
    # Add cover image
    if os.path.exists(cover_img_path):
        story.extend(image_or_placeholder(cover_img_path, width=170*mm, height=90*mm, caption=""))
    else:
        story.append(Spacer(1, 30*mm))
    story.append(Spacer(1, 6*mm))
    # Title block with background color simulated via table
    cover_data = [
        [Paragraph('<font color="#ffffff" size="28"><b>SENDERSMS</b></font><br/><font color="#ffffff" size="16">Restaurant Edition</font>', ParagraphStyle('ct', parent=styles['Normal'], alignment=TA_CENTER, textColor=white, fontName='Helvetica-Bold'))]
    ]
    cover_table = Table(cover_data, colWidths=[170*mm])
    cover_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), COLORS["primary_dark"]),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('LEFTPADDING', (0,0), (-1,-1), 12),
        ('RIGHTPADDING', (0,0), (-1,-1), 12),
        ('TOPPADDING', (0,0), (-1,-1), 14),
        ('BOTTOMPADDING', (0,0), (-1,-1), 14),
        ('ROUNDEDCORNERS', [6,6,6,6]),
    ]))
    story.append(cover_table)
    story.append(Spacer(1, 6*mm))
    story.append(p('<font size="14" color="#075e54"><b>Complete User Guide &amp; Playbook</b></font><br/><font size="9" color="#54656f">Bulk SMS for Nigerian Restaurant Brands • WhatsApp-style Inbox • Templates • Sequences • Campaigns • Scheduling • Analytics</font>', ParagraphStyle('cover2', parent=styles['Normal'], alignment=TA_CENTER, leading=13)))
    story.append(Spacer(1, 4*mm))
    story.append(HRFlowable(width="40%", thickness=1, color=COLORS["primary"], spaceAfter=4, spaceBefore=4, hAlign='CENTER'))
    story.append(p('Featuring playbooks for <b>Chicken Republic</b> • <b>Domino\'s Pizza</b> • <b>Sweet Sensation</b> • <b>The Place</b> • <b>Kilimanjaro</b> • <b>Mama Gold</b> &amp; more<br/>Lagos • Abuja • Port Harcourt • Ibadan • Kano', ParagraphStyle('cover3', parent=styles['Normal'], alignment=TA_CENTER, fontSize=8, leading=11, textColor=COLORS["gray"])))
    story.append(Spacer(1, 8*mm))
    # Info box
    info_data = [
        [Paragraph('<b>Version</b> 2.1 — August 2026', s_small), Paragraph('<b>Audience</b> Restaurant marketers, branch managers, ops', s_small)],
        [Paragraph('<b>Platform</b> Web (desktop + mobile) + SMS-Gate SIM', s_small), Paragraph('<b>Support</b> In-app → Settings • hello@sendsms.ng', s_small)],
    ]
    info_tbl = Table(info_data, colWidths=[85*mm,85*mm])
    info_tbl.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), COLORS["gray_light"]),
        ('BOX', (0,0), (-1,-1), 0.5, COLORS["gray_border"]),
        ('INNERGRID', (0,0), (-1,-1), 0.5, COLORS["gray_border"]),
        ('LEFTPADDING', (0,0), (-1,-1), 8),
        ('RIGHTPADDING', (0,0), (-1,-1), 8),
        ('TOPPADDING', (0,0), (-1,-1), 6),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ('ROUNDEDCORNERS', [6,6,6,6]),
    ]))
    story.append(info_tbl)
    story.append(Spacer(1, 8*mm))
    story.append(p('This guide shows <b>every screen, every button, and every restaurant-ready template</b> — so your team can launch a promo blast in under 10 minutes and handle replies like WhatsApp.', ParagraphStyle('cover4', parent=styles['Normal'], alignment=TA_CENTER, fontSize=8.5, leading=12, textColor=COLORS["dark"], borderPadding=(8,8,8,8), backColor=HexColor("#fff8c4"), borderColor=HexColor("#ffecb3"), borderWidth=0.5)))
    story.append(PageBreak())

    # TABLE OF CONTENTS
    story.append(p("Contents", s_h1))
    story.append(HRFlowable(width="100%", thickness=1.5, color=COLORS["primary_dark"], spaceAfter=8))
    toc = [
        ["1", "Welcome — Why SMS still wins for restaurants", "4"],
        ["2", "Quick Start (10-minute launch)", "5"],
        ["3", "Dashboard — At a glance", "6"],
        ["4", "Contacts — Your restaurant database", "7"],
        ["5", "Lists — Group by city, brand, or visit frequency", "9"],
        ["6", "Templates — Restaurant-ready messages with variables", "10"],
        ["7", "Sequences — Automated follow-ups that feel human", "13"],
        ["8", "Campaigns — Bulk blasts to thousands", "15"],
        ["9", "Send SMS — One-to-one, bulk & scheduled", "17"],
        ["10", "Inbox — WhatsApp-style replies (mobile-first)", "19"],
        ["11", "Auto-Reply — Instant answers to FAQs", "21"],
        ["12", "Follow-Ups — Never miss a hot lead", "22"],
        ["13", "Analytics — What to track & improve", "23"],
        ["14", "Settings, Gateway & SIM", "24"],
        ["15", "Restaurant Brand Playbooks (6 ready-to-copy)", "25"],
        ["16", "Scheduling & Failed — What happens at 9 AM", "28"],
        ["17", "Best Practices & Compliance (STOP, timing, cost)", "29"],
        ["18", "Troubleshooting & FAQ", "31"],
        ["19", "Glossary & Support", "32"],
    ]
    toc_data = []
    for row in toc:
        num, title, pg = row
        toc_data.append([
            Paragraph(f"<b>{num}</b>", ParagraphStyle('tocnum', parent=styles['Normal'], fontSize=8, textColor=COLORS["primary_dark"], fontName='Helvetica-Bold', alignment=TA_RIGHT)),
            Paragraph(title, ParagraphStyle('toctitle', parent=styles['Normal'], fontSize=9, textColor=COLORS["dark"], fontName='Helvetica')),
            Paragraph(pg, ParagraphStyle('tocpg', parent=styles['Normal'], fontSize=8, textColor=COLORS["gray"], alignment=TA_RIGHT)),
        ])
    toc_tbl = Table(toc_data, colWidths=[10*mm, 140*mm, 20*mm])
    toc_tbl.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('LEFTPADDING', (0,0), (-1,-1), 4),
        ('RIGHTPADDING', (0,0), (-1,-1), 4),
        ('TOPPADDING', (0,0), (-1,-1), 3),
        ('BOTTOMPADDING', (0,0), (-1,-1), 3),
        ('LINEBELOW', (0,0), (-1,-1), 0.25, COLORS["gray_border"]),
    ]))
    story.append(toc_tbl)
    story.append(Spacer(1, 6*mm))
    story.append(p('<b>How to use this guide:</b> Read cover-to-cover once, then keep it open in a tab. Every section ends with a <font color="#00a884"><b>Do this now</b></font> checklist. Restaurant examples are ready to copy-paste — just replace the promo code.', s_body))
    story.append(p('<b>Icons:</b> &nbsp; <font color="#00a884">●</font> Action &nbsp; <font color="#ffad1f">●</font> Tip &nbsp; <font color="#ea4335">●</font> Warning &nbsp; <font color="#075e54">●</font> Restaurant example', s_small))
    story.append(PageBreak())

    # 1 - Welcome
    story.append(p("1 &nbsp; Welcome — Why SMS still wins for restaurants", s_h1))
    story.append(HRFlowable(width="30%", thickness=2, color=COLORS["primary"], spaceAfter=8, hAlign='LEFT'))
    story.append(p("Nigerian diners live on their phones, but email is ignored and Instagram DMs disappear. <b>SMS gets read in under 3 minutes — 98% open rate</b> — even without data. For a restaurant, that means a Tuesday 11 AM promo can fill tables by 7 PM.", s_body))
    story.append(p("SENDERSMS was built for operators who manage <b>hundreds of branches and thousands of regulars</b> — not marketers who love jargon. Every screen works on a Tecno phone the same as a MacBook.", s_body))
    story.append(p("Restaurant Brands You'll See Here", s_h2))
    story.append(p("We use six fictional-but-realistic Nigerian restaurant partners as your recipients throughout. Swap in your own names and you're ready:", s_body))
    brand_data = [
        ["Brand", "Type", "Example Locations", "Typical Use Case"],
        ["Chicken Republic", "QSR — Chicken & rice", "Lekki, Ikeja, Surulere", "Weekly promo, new outlet alert"],
        ["Domino's Pizza", "Pizza delivery", "Victoria Island, Yaba, Gbagada", "Combo deal, late-night push"],
        ["Sweet Sensation", "Fast food & bakery", "Ogudu, Festac, Ikoyi", "Breakfast combo, cake order"],
        ["The Place", "Restaurant & lounge", "Lekki, Wuse, GRA", "Friday night live, buffet"],
        ["Kilimanjaro", "Fast food", "Port Harcourt, Enugu, Abuja", "Student discount, branch opening"],
        ["Mama Gold", "Local & continental", "Ibadan, Abeokuta, Ilorin", "Soup of the day, bulk order"],
    ]
    # wrap
    wrapped_brand = []
    for r in brand_data:
        wrapped_brand.append([Paragraph(f"<b>{c}</b>" if wrapped_brand==[] else c, s_table_cell) if isinstance(c,str) else c for c in r])
    # Fix header separately
    brand_table_data = []
    for i, r in enumerate(brand_data):
        row = []
        for c in r:
            style = s_table_header if i==0 else s_table_cell
            row.append(Paragraph(f"<b>{c}</b>" if i==0 else c, style))
        brand_table_data.append(row)
    story.append(make_table(brand_table_data, [35*mm, 35*mm, 45*mm, 55*mm]))
    story.append(Spacer(1,2*mm))
    story.append(p("<b>Result:</b> Every template, sequence and campaign in this guide is pre-filled for these brands — so you can copy, tweak one word, and send.", s_small))
    story.append(p("What you can do in one place", s_h2))
    story.append(p("Contacts → Lists → Templates → Sequences → Campaigns → Send → Inbox → Analytics. That’s the whole loop. Most days you'll live in <b>Send</b> and <b>Inbox</b> (both WhatsApp-style on mobile).", s_body))
    bullets = [
        "<b>Import 5,000 restaurant contacts</b> from CSV in 30 seconds (phone normalization + deduplication built-in).",
        "<b>Personalize</b> with {{first_name}}, {{business_name}}, {{city}} — no mail-merge pain.",
        "<b>Schedule at 9 AM WAT</b> and go live automatically; watch <b>Sent / Failed</b> tabs for proof.",
        "<b>Reply like WhatsApp</b> on your phone — no zooming, no tiny buttons. Unread = green dot + number.",
        "<b>Auto-reply</b> when someone texts “MENU” or “PRICE” while you’re busy.",
    ]
    for b in bullets:
        story.append(bullet(b))
    story.append(p('<b>Do this now:</b> Open your phone, log in, and check that the dashboard shows your gateway as <font color="#00a884"><b>Healthy</b></font>. If it says “No gateway” go to Settings → Gateway now — nothing else will send.', s_quote))

    # 2 Quick Start
    story.append(p("2 &nbsp; Quick Start — Launch a promo in 10 minutes", s_h1))
    story.append(HRFlowable(width="30%", thickness=2, color=COLORS["primary"], spaceAfter=8, hAlign='LEFT'))
    story.append(p("Follow these 8 steps. We’ll use <b>Chicken Republic — 20% Off Family Feast</b> as the running example. Total time: ~10 minutes.", s_body))
    steps = [
        ["1", "Import contacts", "Contacts → Import CSV → Map phone → business_name → city → Add to list ‘Lagos QSR’"],
        ["2", "Create list", "Lists → Create ‘Lagos QSR – Active’ → Add the imported contacts (or let import auto-add)."],
        ["3", "Template", "Templates → New → Name: ‘CR Family Feast’ → Paste template below → Save."],
        ["4", "Campaign", "Campaigns → New → List: Lagos QSR – Active → Template: CR Family Feast → Validate."],
        ["5", "Schedule", "Campaigns → Schedule → Pick tomorrow 11:00 AM (your time) → Schedule."],
        ["6", "Send test", "Send → Pick one contact (your number) → Send Now → Check phone."],
        ["7", "Monitor", "Campaigns shows Scheduled → Running → Completed. Inbox shows replies."],
        ["8", "Reply", "Inbox → Tap chat → Reply. Green bubble = you. Red ‘failed’ shows error; retry from Failed tab."],
    ]
    step_data = [[Paragraph(f"<b>{r[0]}</b>", s_table_cell_center), Paragraph(r[1], ParagraphStyle('sc', parent=s_table_cell, fontName='Helvetica-Bold', textColor=COLORS["primary_dark"])), Paragraph(r[2], s_table_cell)] for r in steps]
    header = [Paragraph("<b>#</b>", s_table_header), Paragraph("<b>Step</b>", s_table_header), Paragraph("<b>Do this</b>", s_table_header)]
    story.append(make_table([header] + step_data, [10*mm, 35*mm, 125*mm]))
    story.append(Spacer(1,3*mm))
    story.append(p("Template to copy for Quick Start:", s_h3))
    story.append(code_block("Hi {{first_name}}! 🍗 Chicken Republic {{city}} — Family Feast is 20% OFF today only!\n4 pcs chicken + chips + 2 drinks = ₦8,999 (was ₦11,200).\nShow this SMS at any Chicken Republic in {{city}}.\nReply MENU for full menu. STOP to opt out.\n— Chicken Republic"))
    story.append(p("Variables in {{double braces}} are replaced per contact. {{city}} for someone in Lekki becomes “Lekki”. If a field is empty we gracefully skip it.", s_small))
    story.extend(image_or_placeholder("docs/images/campaign_dashboard.jpg", width=150*mm, height=75*mm, caption="Fig. 2.1 — Campaigns dashboard with Scheduled badge and Send-at time localized to your timezone"))

    # 3 Dashboard
    story.append(p("3 &nbsp; Dashboard — At a glance", s_h1))
    story.append(HRFlowable(width="30%", thickness=2, color=COLORS["primary"], spaceAfter=8, hAlign='LEFT'))
    story.append(p("The dashboard answers: <b>Are we sending? Are people replying? What needs attention today?</b> It refreshes every time you open it.", s_body))
    dash_cards = [
        ["Card", "What it shows", "Restaurant tip"],
        ["Total Contacts", "All contacts ever imported", "Aim for 1k per outlet; archive closed branches quarterly"],
        ["Messages Sent / Delivered", "Gateway accepted / network confirmed", "Delivered < 90% → check SIM credit / gateway logs"],
        ["Replies & Interested", "People who texted back + marked ★", "Interested = hot — call within 1 hour"],
        ["Active Campaigns", "Running / Scheduled right now", "Never have 2 scheduled to same list on same day"],
        ["Follow-ups Due Today", "Sequence waits expiring today", "Sweet Sensation: Day-3 ‘Did you enjoy?’ nudge"],
        ["Gateway Status", "Healthy / Unhealthy + last error", "If ‘offline’ → Settings → SIM / Credentials"],
    ]
    hdr = [Paragraph(f"<b>{c}</b>", s_table_header) for c in dash_cards[0]]
    dash_rows = [hdr] + [[Paragraph(c, s_table_cell) for c in r] for r in dash_cards[1:]]
    story.append(make_table(dash_rows, [40*mm, 60*mm, 70*mm]))
    story.append(p("<b>Do this now:</b> Check “Lead Distribution” pie. If “new” is >70%, you’re importing but not talking. Create a 3-step sequence today.", s_quote))

    # 4 Contacts
    story.append(p("4 &nbsp; Contacts — Your restaurant database", s_h1))
    story.append(HRFlowable(width="30%", thickness=2, color=COLORS["primary"], spaceAfter=8, hAlign='LEFT'))
    story.append(p("A contact is one diner, corporate client, or branch — anyone with a phone. We store NGN numbers as <b>+234…</b> but you can type <b>0803…</b> everywhere; we normalize automatically.", s_body))
    story.extend(image_or_placeholder("docs/images/contacts_mobile.jpg", width=85*mm, height=110*mm, caption="Fig. 4.1 — Contacts on mobile: WhatsApp-style cards with big Message / Delete buttons — no zoom needed. Desktop shows a table; mobile shows cards."))
    story.append(p("Mobile vs Desktop", s_h2))
    story.append(p("On your phone you see <b>cards</b> (avatar + phone + business + slate). On desktop you see a <b>table</b>. The data is identical; only the layout changes. Both let you:", s_body))
    for b in [
        "<b>Search</b> by name, business (e.g. “Domino’s”), or phone (type 0803 or +234 — both find it).",
        "<b>Filter by status</b>: new → contacted → replied → interested → follow-up → meeting → customer → not_interested / closed.",
        "<b>Select multiple</b> (tap the checkbox circle on avatar) → Delete, Change status, or Add to List in one go.",
        "<b>Message</b> instantly: tap green <b>Message</b> → WhatsApp-style composer slides up with live preview → Send. Failed? See red bubble + retry.",
        "<b>Delete</b> with the red trash — confirm once, done.",
    ]:
        story.append(bullet(b))
    story.append(p("Importing — CSV that just works", s_h2))
    story.append(p("Export from Google Sheets, Excel, or your POS. Required: one column with phones. Optional: first_name, last_name, business_name, city, state, email, website, industry, source.", s_body))
    story.append(p("Steps:", s_h3))
    story.append(bullet("Contacts → Import → Drop CSV → We auto-detect headers (phone, business, city…) → Adjust mapping if needed → Choose list → Import."))
    story.append(bullet("We show <b>Imported / Duplicates / Invalid</b> counts. Duplicates are skipped; invalid numbers are listed with reason."))
    story.append(bullet("Large files? Split to 5k rows per file for faster feedback."))
    example_csv = "first_name,last_name,business_name,phone_number,city,state\nFunke, Adebayo, Chicken Republic,08031234567,Lekki,Lagos\nTunde, Okon, Domino's Pizza,08039876543,Victoria Island,Lagos\nAisha, Musa, Mama Gold,08055551234,Ibadan,Oyo"
    story.append(code_block(example_csv))
    story.append(p("Phone normalization examples:", s_h3))
    phone_tbl = [
        ["You type", "We store", "Findable by"],
        ["08031234567", "+2348031234567", "0803, 234803, +234…"],
        ["+2348031234567", "+2348031234567", "0803…"],
        ["2348031234567", "+2348031234567", "0803…"],
        ["8031234567", "+2348031234567", "0803…"],
    ]
    story.append(make_table([[Paragraph(c, s_table_header if i==0 else s_table_cell) for c in r] for i,r in enumerate(phone_tbl)], [45*mm, 45*mm, 80*mm]))
    story.append(p("<b>Do this now:</b> Import 20 test contacts with your own team numbers + 15 real restaurant names (use the CSV above). Search “803” → should find all.", s_quote))
    story.append(p("<font color=\"#ea4335\"><b>Warning:</b></font> Never import lists you didn’t collect. Cameroon/UK numbers are accepted for storage but delivery depends on your gateway’s international support.", s_small))

    # 5 Lists
    story.append(p("5 &nbsp; Lists — Group by city, brand, or frequency", s_h1))
    story.append(HRFlowable(width="30%", thickness=2, color=COLORS["primary"], spaceAfter=8, hAlign='LEFT'))
    story.append(p("A list is a named group — e.g. <b>Lagos QSR – Active (842)</b>, <b>Abuja Corporate LUNCH (210)</b>, <b>Kilimanjaro Students Enugu (1,430)</b>. A contact can be in many lists.", s_body))
    story.append(p("Why lists matter for restaurants:", s_h2))
    for b in [
        "<b>Geography:</b> “Lekki vs Ikeja” — send Ikeja opening promo only to Ikeja contacts.",
        "<b>Brand:</b> Chicken Republic fans vs Domino’s pizza lovers — different menus.",
        "<b>Recency:</b> “Visited last 30 days” vs “Lapsed 90 days” — different offers.",
        "<b>Value:</b> Corporate bulk-order clients vs walk-in students.",
    ]:
        story.append(bullet(b))
    story.append(p("Creating & managing", s_h2))
    story.append(bullet("Lists → Create → Name: “Sweet Sensation – Breakfast Club” → View → Add Contacts → Search → tick → Add."))
    story.append(bullet("View a list → see all members → Remove one with trash (does NOT delete contact, just from list)."))
    story.append(bullet("Delete a list ≠ delete contacts. Contacts stay; only grouping is removed."))
    story.append(p("<b>Tip:</b> Name lists like <b>City – Brand – Segment – Size</b>: e.g. <b>Lagos – All Brands – VIP – 340</b>. You’ll thank yourself at 8 AM when scheduling.", s_quote))

    # 6 Templates
    story.append(p("6 &nbsp; Templates — Restaurant-ready messages with variables", s_h1))
    story.append(HRFlowable(width="30%", thickness=2, color=COLORS["primary"], spaceAfter=8, hAlign='LEFT'))
    story.append(p("A template is a reusable SMS skeleton with <b>{{variables}}</b> that personalize per contact. Save once, reuse in campaigns, sequences, or one-off sends. SMS counts (segments) are shown live.", s_body))
    story.extend(image_or_placeholder("docs/images/templates_sequences.jpg", width=150*mm, height=70*mm, caption="Fig. 6.1 — Template editor & Sequence builder: write once, personalize for every restaurant branch"))
    story.append(p("Variables — your personalization toolkit", s_h2))
    var_tbl = [
        ["Variable", "Replaced with", "Example (Funke at Chicken Republic Lekki)"],
        ["{{first_name}}", "Contact first name", "Funke"],
        ["{{last_name}}", "Last name", "Adebayo"],
        ["{{business_name}}", "Business / restaurant name", "Chicken Republic"],
        ["{{city}}", "City", "Lekki"],
        ["{{state}}", "State", "Lagos"],
        ["{{phone_number}}", "Full phone", "+2348031234567"],
        ["{{website}}", "Website if stored", "https://chicken-republic.com"],
        ["{{industry}}", "Industry tag", "QSR"],
    ]
    story.append(make_table([[Paragraph(c, s_table_header if i==0 else s_table_cell) for c in r] for i,r in enumerate(var_tbl)], [35*mm, 45*mm, 90*mm]))
    story.append(p("If a field is empty we trim gracefully: “Hi {{first_name}}” when first_name is missing becomes “Hi there” (space collapsed). Always include a fallback like “there” in your copy if many contacts lack names.", s_small))
    story.append(p("Restaurant Template Library — Copy/Paste", s_h2))
    story.append(p("All under 160 chars where possible (1 segment = cheapest). Emoji = 1 char but forces UCS-2 (70-char limit). We show segment count live — watch it.", s_body))
    templates = [
        ["1. Flash Promo (all brands)", "Hi {{first_name}}! 🍗 {{business_name}} {{city}}: 20% OFF today only — Family Feast ₦8,999. Show this SMS. Reply MENU. STOP to opt out."],
        ["2. New Branch (Kilimanjaro)", "Hello {{first_name}}, Kilimanjaro now open in {{city}}! 🎉 Free chapman on first visit this week. {{business_name}} — {{phone_number}}. STOP to stop."],
        ["3. Pizza Combo (Domino's)", "Hey {{first_name}}! 🍕 Domino's {{city}} — Large pizza + 2 drinks = ₦9,500 till 9PM. Order: 0700-DOMINOS. STOP opt out."],
        ["4. Breakfast Push (Sweet Sensation)", "Good morning {{first_name}}! Sweet Sensation {{city}} — Jollof + Chicken + Drink = ₦2,800 till 11AM. Walk in or call {{phone_number}}. STOP."],
        ["5. Buffet / Lounge (The Place)", "Hi {{first_name}}, The Place {{city}} Friday Buffet is LIVE — ₦7,500 all-you-can-eat 7-11PM. Reserve: {{phone_number}}. Reply RESERVE. STOP."],
        ["6. Corporate Bulk (Mama Gold)", "Hello {{business_name}}, Mama Gold {{city}} handles bulk orders from 20+ packs with free delivery in {{state}}. Quote? Reply BULK. STOP."],
        ["7. Feedback (all)", "{{first_name}}, enjoyed {{business_name}} {{city}}? Tap to rate 1-5: reply 5=Love it! We read every reply. STOP."],
        ["8. Re-engage Lapsed", "We miss you, {{first_name}}! 😊 {{business_name}} {{city}} — come back & get 15% OFF your next order. Code: COMEBACK15. STOP."],
        ["9. Birthday (if you store DOB in notes)", "Happy Birthday {{first_name}}! 🎂 Sweet Sensation {{city}} gifts you ₦1,500 OFF cake today. Show ID + this SMS. STOP."],
    ]
    for t in templates:
        title, body = t
        story.append(p(f"<b>{title}</b>", s_h3))
        story.append(p(f'“{body}”', ParagraphStyle('tplbody', parent=s_body, fontSize=8.5, leading=12, textColor=COLORS["dark2"], backColor=HexColor("#f6f8fa"), borderPadding=(6,6,6,6))))
        cc, seg = (len(body), 1) if len(body)<=160 else (len(body), 2)
        story.append(p(f"{len(body)} chars • {1 if len(body)<=160 else 2} SMS • ~₦{4 if len(body)<=160 else 8}", ParagraphStyle('tplmeta', parent=s_small, alignment=TA_RIGHT)))
    story.append(p("<b>Do this now:</b> Create 3 templates: one Promo (1), one Re-engage (8), one Feedback (7). Use Preview with Funke / Chicken Republic / Lekki to see real output.", s_quote))
    story.append(p('<font color="#ea4335"><b>Cost warning:</b></font> One “long” 306-char promo = 2 SMS segments = 2× cost. Keep promos ≤160 where possible. We flag “long messages cost more” automatically.', s_small))

    # 7 Sequences
    story.append(p("7 &nbsp; Sequences — Automated follow-ups that feel human", s_h1))
    story.append(HRFlowable(width="30%", thickness=2, color=COLORS["primary"], spaceAfter=8, hAlign='LEFT'))
    story.append(p("A sequence is a drip: <b>Send → Wait → Condition → Send/Stop</b>. The gateway sends the first SMS; the sequence watches for a reply, then decides next step. Perfect for restaurants where the first promo rarely closes — the follow-up does.", s_body))
    story.append(p("Example: Chicken Republic 3-step nudge", s_h2))
    seq_steps = [
        ["Step", "Type", "Config", "What happens"],
        ["0", "send_sms", "Template: Flash Promo", "11 AM — Send Family Feast promo"],
        ["1", "wait", "48 hours", "Pause 2 days. If reply → pause sequence"],
        ["2", "condition", "contact_replied ?", "If YES → Stop (they replied, you handle in Inbox). If NO → next step"],
        ["3", "send_sms", "Template: Comeback 15% OFF", "Day 3 — Gentle nudge to non-responders"],
        ["4", "wait", "72 hours", "Wait 3 more days"],
        ["5", "send_sms", "Template: Feedback", "Day 6 — “Enjoyed us?” — moves to Inbox if they reply"],
        ["6", "stop", "—", "End. Mark as completed."],
    ]
    story.append(make_table([[Paragraph(c, s_table_header if i==0 else s_table_cell) for c in r] for i,r in enumerate(seq_steps)], [12*mm, 22*mm, 45*mm, 91*mm]))
    story.append(p("Conditions you can branch on:", s_h3))
    story.append(bullet("<b>contact_replied</b> — true if any inbound SMS arrived since last step."))
    story.append(bullet("<b>contact_did_not_reply</b> — inverse."))
    story.append(bullet("<b>message_delivered / message_failed</b> — after gateway confirms."))
    story.append(bullet("<b>contact_opted_out</b> — STOP keyword → auto-stop (compliance)."))
    story.append(p("Sequence tips for restaurants:", s_h2))
    for b in [
        "Keep sequences <b>short (3–4 messages max)</b>. Lagos diners ignore the 7th nudge.",
        "Vary the offer: Promo → Reminder → Feedback is better than three discounts.",
        "Set <b>wait 48h</b> minimum between promos; daily pings feel spammy.",
        "Add a <b>STOP exit</b> — opt-out instantly kills pending steps (we do this automatically).",
        "Clone a winner: Domino’s 2-step pizza sequence cloned for Chicken Republic with one word change.",
    ]:
        story.append(bullet(b))
    story.append(p("<b>Do this now:</b> Create Sequence “CR 3-Step Nudge” with the 7 rows above. Attach it to a campaign (optional) or trigger via API.", s_quote))

    # 8 Campaigns
    story.append(p("8 &nbsp; Campaigns — Bulk blasts to thousands", s_h1))
    story.append(HRFlowable(width="30%", thickness=2, color=COLORS["primary"], spaceAfter=8, hAlign='LEFT'))
    story.append(p("A campaign = <b>List + Message (template or inline body) + optional Sequence + Schedule</b>. It creates one CampaignContact per list member, then sends in batches of 50 via gateway. Status flows: <b>draft → scheduled → running → completed</b> (or paused/stopped/failed).", s_body))
    story.append(p("Lifecycle & controls", s_h2))
    camp_tbl = [
        ["Status", "What it means", "You can do"],
        ["draft", "Being edited; not validated", "Edit, change list/message, Delete"],
        ["scheduled", "Validated; waiting for Start time", "Edit (returns to draft), Reschedule, Start now"],
        ["running", "Sending batches; polling gateway", "Pause, Stop"],
        ["paused", "Temporarily halted", "Resume, Stop"],
        ["completed", "All contacts processed", "View analytics, Duplicate"],
        ["stopped", "Manually halted", "Duplicate to restart"],
        ["failed", "Validation failed at launch", "Fix reason, move to draft"],
    ]
    story.append(make_table([[Paragraph(c, s_table_header if i==0 else s_table_cell) for c in r] for i,r in enumerate(camp_tbl)], [25*mm, 75*mm, 70*mm]))
    story.append(p("Scheduling — precise to the minute", s_h2))
    story.append(p("Pick a future time in <b>your local timezone</b> (we store UTC). Example: schedule for <b>Tomorrow 09:00 WAT</b> → we convert to 08:00 UTC → fire at 09:00 Lagos. A tiny “Will send at…” line confirms.", s_body))
    story.append(bullet("Campaigns → New → Choose list (e.g. Lagos QSR) → Write message or pick template → Create Draft → Validate → Schedule → Pick datetime → Schedule."))
    story.append(bullet("Reschedule any time while scheduled. Editing content returns to draft (so you must re-validate)."))
    story.append(bullet("Two launch paths: <b>Celery beat every minute</b> + <b>inline poller every 30s</b> (so Render free tier still fires even if worker sleeps). Only one wins via atomic claim — no double-send."))
    story.append(p("Duplicating — the smart way to iterate", s_h3))
    story.append(p("Duplicate a campaign to get <b>“Name (Copy)”</b> with all sending rules (daily/hourly limits, delays, weekend flag) intact but counters reset. Edit the copy, not the running original.", s_body))
    story.append(p("<b>Do this now:</b> Create a draft campaign “CR Family Feast – Lagos – Test” to 10 contacts (your team). Validate → Schedule +5 min → watch it auto-run to Completed. Check Campaign analytics.", s_quote))

    # 9 Send SMS
    story.append(p("9 &nbsp; Send SMS — One-to-one, bulk &amp; scheduled", s_h1))
    story.append(HRFlowable(width="30%", thickness=2, color=COLORS["primary"], spaceAfter=8, hAlign='LEFT'))
    story.append(p("Send is for <b>quick, non-campaign sends</b> — a single reply, a VIP blast, or a timed push. Campaigns are for large, tracked blasts; Send is for speed.", s_body))
    story.append(p("Modes", s_h2))
    mode_tbl = [
        ["Mode", "How", "Example"],
        ["Pick Contact", "Search → tick 1..n contacts", "Send Family Feast to 3 VIP Chicken Republic regulars"],
        ["Enter Number", "Type raw number (080…)", "Hot lead from flyer — not yet in contacts"],
        ["Send to List", "Choose a saved list", "All 1,430 Kilimanjaro Enugu students"],
    ]
    story.append(make_table([[Paragraph(c, s_table_header if i==0 else s_table_cell) for c in r] for i,r in enumerate(mode_tbl)], [30*mm, 60*mm, 80*mm]))
    story.append(p("Now vs Schedule", s_h2))
    story.append(bullet("<b>Now</b>: immediate gateway send. We show <b>Sent / Failed / Total</b> plus per-recipient error & raw gateway JSON."))
    story.append(bullet("<b>Schedule</b>: pick Date + Time (local). We store as UTC, show confirmation. Message sits as <b>pending</b> until due, then auto-sends and creates a Message bubble so it appears in Inbox."))

    story.append(p("Tabs you’ll see after sending", s_h3))
    tab_tbl = [
        ["Tab", "Source", "Shows", "Actions"],
        ["Scheduled", "ScheduledMessage pending", "All pending futures, soonest first", "Cancel (only while pending)"],
        ["Sent", "Message outgoing sent/delivered", "Recent successes with delivered check", "—"],
        ["Failed", "Message failed + Scheduled failed", "Gateway errors, offline SIM, bad number", "Retry instantly"],
    ]
    story.append(make_table([[Paragraph(c, s_table_header if i==0 else s_table_cell) for c in r] for i,r in enumerate(tab_tbl)], [28*mm, 42*mm, 60*mm, 40*mm]))
    story.append(p("Failed? Here’s what to do:", s_h3))
    for b in [
        "Open <b>Failed</b> → read red error: “Timed out” (gateway), “No credentials” (Settings), “Device offline” (phone app).",
        "Fix cause → tap <b>↻ Retry</b> → we re-send via gateway immediately (new provider ID).",
        "Scheduled that failed before gateway (opt-out/suppression) also shows — retry moves it to pending → send at next 30s poll.",
        "Inbox also shows failures as a red “⚠ failed” bubble inside the chat — so you see context (what you sent to whom).",
    ]:
        story.append(bullet(b))
    story.append(p("<b>Timezone warning:</b> We fixed the old bug where Schedule used hardcoded +01:00. Now we send <b>UTC ISO via toISOString()</b> — so Accra (GMT) and Lagos (WAT) both fire at the local wall time you picked.", s_quote))
    story.append(p("<b>Do this now:</b> Send → Schedule a “Hello {{first_name}}” to your own number +5 min → switch to Scheduled tab → watch pending → after fire, check Sent (+ inbox bubble) or Failed + Retry.", s_quote))

    # 10 Inbox
    story.append(p("10 &nbsp; Inbox — WhatsApp-style replies (mobile-first)", s_h1))
    story.append(HRFlowable(width="30%", thickness=2, color=COLORS["primary"], spaceAfter=8, hAlign='LEFT'))
    story.extend(image_or_placeholder("docs/images/inbox_whatsapp.jpg", width=150*mm, height=80*mm, caption="Fig. 10.1 — Inbox: left chat list (avatar, preview, unread green badge) • right WhatsApp bubbles (green = you, white = diner). Mobile shows list OR chat full-screen; desktop shows both. No zoom needed — 16px base, 44px tap targets."))

    story.append(p("What makes it WhatsApp:", s_h2))
    for b in [
        "<b>Green header (#f0f2f5 / #202c33)</b> with Sync ↻ + diagnostic bug icon.",
        "<b>Search bar</b> — instant filter by name, business, phone, or message preview (Nigerian number variants auto-expanded).",
        "<b>Filter chips</b> (All / Unread / Interested / Closed / Failed) — scroll horizontally, green = active.",
        "<b>Chat rows</b>: 49px avatar (initials), bold name if unread, time (or “Tue” if >24h), green unread circle with count, blue double-check if you replied.",
        "<b>Chat view</b>: WhatsApp #efeae2 doodle background, green outgoing bubbles (#d9fdd3) vs white incoming, tick marks (✓ sent, ✓✓ delivered blue), red “⚠ failed” under bubble with error.",
        "<b>Composer</b>: rounded white bar + green circular Send (becomes Mic when empty) — thumb-friendly.",
        "<b>Stale? No</b>: auto-polls every 8s; Sync ↻ re-registers webhooks & replays history.",
    ]:
        story.append(bullet(b))
    story.append(p("Read vs Unread — how it really works", s_h2))
    story.append(p("Opening a chat <b>clears the unread badge</b> but <b>preserves labels</b> (Interested / Closed). We fixed the old bug where every read reset Status to “read” and erased Interested.", s_body))
    story.append(p("Statuses you’ll set:", s_h3))
    status_tbl = [
        ["Action", "Conversation →", "Contact lead_status →", "Meaning"],
        ["Star ★", "interested", "interested", "Hot lead — prioritize, call soon"],
        ["ThumbsDown", "not_interested", "not_interested", "Not now — suppress future promos"],
        ["Archive", "closed", "closed", "Done — no more follow-ups"],
        ["Mark unread", "unread + count=1", "—", "Remind yourself to reply later"],
    ]
    story.append(make_table([[Paragraph(c, s_table_header if i==0 else s_table_cell) for c in r] for i,r in enumerate(status_tbl)], [25*mm, 35*mm, 35*mm, 75*mm]))
    story.append(p("Sync & receive — the full picture", s_h2))
    story.append(p("SMS-Gate has <b>NO endpoint to fetch inbound SMS text</b>. The ONLY way in is <b>webhooks</b> (POST /webhooks {url, event}) + <b>inbox export</b> (POST /messages/inbox/export {deviceId, since, until}). That’s why:", s_body))
    for b in [
        "On first install or domain change we auto-register <b>8 events</b> (sms:received, sms:sent, delivered, failed, cancelled + mms/data).",
        "Sync ↻ triggers <b>export of last 7 days</b> (overlap 10 min) — phone re-fires sms:received webhooks → your inbox backfills.",
        "Poll-debug (bug icon) checks: PUBLIC_BASE_URL set? Signing secret? Device online? Webhooks registered? Last webhook time?",
        "Delivery receipts (sent → delivered / failed) arrive via same webhooks and update tick marks.",
    ]:
        story.append(bullet(b))
    story.append(p('<b>Do this now:</b> Open Inbox on your phone (no zoom!). Search “0803” → should still find +234 number. Open a chat → send “Test {{first_name}}” → watch green bubble → tap Sync → see tick go blue if delivered.', s_quote))

    # 11 Auto-Reply
    story.append(p("11 &nbsp; Auto-Reply — Instant answers when you're busy", s_h1))
    story.append(HRFlowable(width="30%", thickness=2, color=COLORS["primary"], spaceAfter=8, hAlign='LEFT'))
    story.append(p("Auto-reply answers inbound SMS <b>before you see it</b> — e.g. someone texts “MENU” at 11 PM, they get the menu at 11:00:03 PM, not next morning. Rules are keyword-based with cooldown so you don’t spam a chatter.", s_body))
    story.append(p("How to set up (example: Domino’s MENU)", s_h2))
    story.append(bullet("Auto-Reply → New Rule → Name: “Domino’s MENU” → Trigger: keyword contains “MENU” → Reply: “Domino’s Menu: Margherita ₦4,500, Pepperoni ₦5,200, BBQ Chicken ₦5,800. Order: 0700-DOMINOS. Reply ORDER.” → Cooldown 4h → Save & Enable."))
    story.append(bullet("Test by texting “MENU” from your second phone → should auto-reply in Inbox as a green bubble marked auto-reply."))
    story.append(p("Rules we recommend for restaurants:", s_h3))
    ar_tbl = [
        ["Keyword", "Reply (copy)", "Brand"],
        ["MENU", "Our menu: https://... + hot promo line", "All"],
        ["PRICE / HOW MUCH", "Price list + ‘Reply ORDER + item’ flow", "Kilimanjaro"],
        ["LOCATION / WHERE", "Nearest outlet address + Google Maps link", "The Place"],
        ["COMPLAINT", "Sorry! 🙏 DM 080… or reply DETAILS. We fix it in 1h.", "Mama Gold"],
        ["STOP / UNSUBSCRIBE", "Auto-handled: opts out + adds to suppression + stops sequences", "System (forced)"],
    ]
    story.append(make_table([[Paragraph(c, s_table_header if i==0 else s_table_cell) for c in r] for i,r in enumerate(ar_tbl)], [30*mm, 95*mm, 45*mm]))
    story.append(p("<b>STOP compliance:</b> Any inbound containing STOP (case-insensitive) instantly opts out, adds to suppression list, sets consent_status=opted_out, and cancels pending follow-ups. We NEVER auto-reply to STOP with promo copy.", s_small))
    story.append(p("<b>Do this now:</b> Enable 2 rules: MENU and LOCATION. Set cooldown 2h. Test both.", s_quote))

    # 12 Follow-Ups
    story.append(p("12 &nbsp; Follow-Ups — Never miss a hot lead", s_h1))
    story.append(HRFlowable(width="30%", thickness=2, color=COLORS["primary"], spaceAfter=8, hAlign='LEFT'))
    story.append(p("Follow-ups are sequence-driven tasks: <b>“Send Step 2 to Tunde in 48h unless he replies”</b>. You see them as cards with due dates; they fire automatically unless cancelled.", s_body))
    story.append(p("You’ll see:", s_h2))
    for b in [
        "<b>Pending</b> — waiting for its time.",
        "<b>Sending</b> — being processed.",
        "<b>Sent</b> — handed to gateway.",
        "<b>Cancelled</b> — reply or opt-out stopped it.",
    ]:
        story.append(bullet(b))
    story.append(p("In Inbox, any reply automatically <b>cancels all pending follow-ups</b> for that contact and marks CampaignContact as “replied” so campaign reports a true reply rate (one per contact per campaign, not per “YES YES YES”).", s_small))
    story.append(p("<b>Do this now:</b> Look at Follow-Ups → Due Today. If overdue >5, create a sequence with shorter waits.", s_quote))

    # 13 Analytics
    story.append(p("13 &nbsp; Analytics — What to track &amp; improve", s_h1))
    story.append(HRFlowable(width="30%", thickness=2, color=COLORS["primary"], spaceAfter=8, hAlign='LEFT'))
    story.extend(image_or_placeholder("docs/images/analytics.jpg", width=150*mm, height=70*mm, caption="Fig. 13.1 — Analytics: delivery vs reply vs opt-out by day; drill into Campaign analytics for per-contact status"))
    story.append(p("Overview metrics (choose 7 / 30 / 90 days):", s_h2))
    met_tbl = [
        ["Metric", "Formula", "Restaurant baseline"],
        ["Delivery Rate", "Delivered / Sent", ">95% good; <85% check gateway/SIM credit"],
        ["Reply Rate", "Replies / Sent", "8–15% promo; 25%+ feedback ask"],
        ["Interested Rate", "Interested / Replies", ">60% of replies should become interested"],
        ["Failed", "Gateway rejected", "<5% inevitable (bad numbers); >10% = list hygiene"],
        ["Opt-outs", "STOP keywords", "<1% per blast; >3% = too frequent / irrelevant"],
    ]
    story.append(make_table([[Paragraph(c, s_table_header if i==0 else s_table_cell) for c in r] for i,r in enumerate(met_tbl)], [30*mm, 45*mm, 95*mm]))
    story.append(p("Campaign drill-down adds <b>contact_status breakdown</b>: pending, queued, sent, delivered, failed, replied, opted_out — so you know exactly where each diner sits.", s_body))
    story.append(p("<b>Do this now:</b> After your first 100 sends, note delivery & reply rates. If delivered 100% but replies 0%, your copy is wrong — try Template #7 (feedback).", s_quote))

    # 14 Settings
    story.append(p("14 &nbsp; Settings, Gateway &amp; SIM", s_h1))
    story.append(HRFlowable(width="30%", thickness=2, color=COLORS["primary"], spaceAfter=8, hAlign='LEFT'))
    story.append(p("Gateway = your SMS pipe (SMS-Gate.app). One bad credential = every blast shows 0 delivered. Settings is your health center.", s_body))
    story.append(p("Must-have env vars (Render / Docker):", s_h2))
    env_tbl = [
        ["Variable", "Example", "Why"],
        ["SMSGATE_BASE_URL", "https://api.sms-gate.app/3rdparty/v1", "API root"],
        ["SMSGATE_USERNAME", "your_cloud_username", "NOT website login — Cloud Server creds from app"],
        ["SMSGATE_PASSWORD", "…", "Same"],
        ["SMSGATE_WEBHOOK_SECRET", "…", "From app → Settings → Webhooks → Signing Key"],
        ["PUBLIC_BASE_URL", "https://your-app.onrender.com", "So gateway can POST inbound to /api/v1/webhooks/smsgateway"],
        ["DATABASE_URL", "postgresql+asyncpg://…", "Postgres (Render free → Upstash rediss needs TLS)"],
        ["REDIS_URL", "redis://… or rediss://…", "Celery broker; rediss auto-verifies cert"],
    ]
    story.append(make_table([[Paragraph(c, s_table_header if i==0 else s_table_cell) for c in r] for i,r in enumerate(env_tbl)], [45*mm, 55*mm, 70*mm]))
    story.append(p("SIM selection:", s_h3))
    story.append(p("If your phone has dual SIM, set <b>sim_number 1 or 2</b> in Settings. Scheduled & campaign sends respect this per message.", s_body))
    story.append(p("Webhook health on Settings → Gateway:", s_h2))
    for b in [
        "Green “Healthy” = last gateway test succeeded + device online.",
        "Use “Test Connection” — should be <2s, not a timeout.",
        "If “PUBLIC_BASE_URL not set” warning appears, inbound will always be 0 — fix it before testing replies.",
    ]:
        story.append(bullet(b))
    story.append(p("<b>Do this now:</b> Copy PUBLIC_BASE_URL from your browser bar, paste into env, redeploy, then hit “Test Connection” until Healthy.", s_quote))

    # 15 Playbooks
    story.append(p("15 &nbsp; Restaurant Brand Playbooks — 6 ready-to-copy campaigns", s_h1))
    story.append(HRFlowable(width="30%", thickness=2, color=COLORS["primary"], spaceAfter=8, hAlign='LEFT'))
    story.append(p("Steal these. Each is a <b>List + Templates + Sequence + Schedule line</b>. Replace the promo price/date and send.", s_body))

    playbooks = [
        {
            "title": "A. Chicken Republic — Weekend Family Feast",
            "brand": "Chicken Republic",
            "list": "Lagos – CR Regulars – 2,100",
            "goal": "Fill Saturday lunch",
            "steps": [
                "Template T1 (Fri 11 AM): “Hi {{first_name}}! 🍗 CR {{city}} Family Feast 20% OFF today+tomm — ₦8,999. Show SMS. STOP.”",
                "Wait 24h",
                "If not replied → T2 (Sat 9 AM): “Last call {{first_name}} — Feast ends tonight 9PM. 2 extra drinks free if you order before noon!”",
                "Wait 48h → T3 Feedback: “Enjoyed CR {{city}}? Rate 5=⭐⭐⭐⭐⭐”",
            ],
            "schedule": "Fri 11:00 WAT, Sat 9:00 WAT",
            "kpi": "Delivery >96% • Reply >12% • Interested >7%"
        },
        {
            "title": "B. Domino’s Pizza — Late-Night Combo",
            "brand": "Domino's",
            "list": "VI + Lekki – Pizza Lovers – 840",
            "goal": "9 PM–Midnight orders",
            "steps": [
                "T1 (Daily 8 PM): “Hey {{first_name}}! 🍕 Domino’s {{city}} — Large + 2 drinks ₦9,500 till midnight. 0700-DOMINOS. STOP.”",
                "Condition: if replied “ORDER” → stop (human takes over in Inbox); else wait 3 days → rotate topping promo",
            ],
            "schedule": "Daily 20:00 WAT (use recurring campaign via duplicate)",
            "kpi": "Reply >15% (hungry intent high)"
        },
        {
            "title": "C. Sweet Sensation — Breakfast Club",
            "brand": "Sweet Sensation",
            "list": "Ogudu + Ikeja – Morning – 560",
            "goal": "6–11 AM breakfast traffic",
            "steps": [
                "T1 (Weekday 6:30 AM): “Good morning {{first_name}}! ☀️ SS {{city}} Breakfast Jollof ₦2,800 till 11AM. Walk-in. STOP.”",
                "Wait 3 days → T2: “Missed breakfast? Try our bakery box ₦3,500.”",
            ],
            "schedule": "Tue/Thu 06:30 WAT",
            "kpi": "Reply 8%+ — morning habit"
        },
        {
            "title": "D. The Place — Friday Buffet & Lounge",
            "brand": "The Place",
            "list": "Lekki – Lounge – 430",
            "goal": "Friday 7-11 PM covers",
            "steps": [
                "T1 (Thu 4 PM): “Hi {{first_name}}, The Place {{city}} Friday Buffet LIVE ₦7,500 7-11PM. Reserve? Reply RESERVE. STOP.”",
                "If RESERVE → Auto-reply with Google Maps + “See you 7PM!”",
                "Fri 10 AM follow-up to non-reservers: “2 tables left! Confirm now.”",
            ],
            "schedule": "Thu 16:00 WAT",
            "kpi": "RESERVE keyword 10%+"
        },
        {
            "title": "E. Kilimanjaro — Campus Opening",
            "brand": "Kilimanjaro",
            "list": "Enugu – Students – 1,430",
            "goal": "New outlet footfall",
            "steps": [
                "T1 (Opening day 9 AM): “Hello {{first_name}}, Kilimanjaro now open in {{city}}! Free chapman on first visit this week. STOP.”",
                "Wait 5 days → T2: “Student ID = 10% OFF this week only. Bring a friend!”",
            ],
            "schedule": "Opening day 09:00 WAT",
            "kpi": "Footfall track via code CHAPMAN"
        },
        {
            "title": "F. Mama Gold — Corporate Bulk & Soup of Day",
            "brand": "Mama Gold",
            "list": "Ibadan – Corporate – 210",
            "goal": "Bulk lunch orders 20+ packs",
            "steps": [
                "T1 (Mon 10 AM): “Hello {{business_name}}, Mama Gold {{city}} handles bulk from 20 packs with free delivery in {{state}}. Reply BULK for quote. STOP.”",
                "If BULK → Inbox tag interested + manual quote in 1h",
                "Thu → “Soup of the day: Egusi + Pounded Yam ₦2,200. Order till 2PM.”",
            ],
            "schedule": "Mon 10:00 WAT",
            "kpi": "Bulk reply 20%+ = gold"
        },
    ]
    for pb in playbooks:
        story.append(p(pb["title"], s_h2))
        story.append(p(f"<b>List:</b> {pb['list']} &nbsp;|&nbsp; <b>Goal:</b> {pb['goal']}", ParagraphStyle('pblist', parent=s_small, textColor=COLORS["gray"])))
        for step in pb["steps"]:
            story.append(bullet(step))
        story.append(p(f"<b>Schedule:</b> {pb['schedule']} &nbsp;|&nbsp; <b>KPI:</b> {pb['kpi']}", ParagraphStyle('pbkpi', parent=s_small, textColor=COLORS["primary_dark"])))
        story.append(Spacer(1,2*mm))

    story.append(p("<b>Copy-paste trick:</b> Duplicate a winning campaign → change only template & list. Sending rules (hour limits, weekend flag) stay perfect.", s_quote))

    # 16 Scheduling & Failed deep dive
    story.append(p("16 &nbsp; Scheduling &amp; Failed — What happens at 9 AM", s_h1))
    story.append(HRFlowable(width="30%", thickness=2, color=COLORS["primary"], spaceAfter=8, hAlign='LEFT'))
    story.append(p("Scheduling has two layers — <b>campaign schedule</b> (launch a whole blast) and <b>Send schedule</b> (one-off message). Both share the same guarantee:", s_body))
    story.append(p("<b>If it is pending, you will see it. If it failed, you will know why. If it sent, it is in the chat.</b>", ParagraphStyle('guarantee', parent=s_body, alignment=TA_CENTER, fontName='Helvetica-Bold', textColor=COLORS["primary_dark"], backColor=HexColor("#f0f9f6"), borderPadding=(8,8,8,8), borderColor=COLORS["primary"], borderWidth=0.5)))
    story.append(p("Timeline for a 9:00 AM scheduled message:", s_h2))
    timeline = [
        ["Time", "System", "What you see"],
        ["Now", "You hit Schedule", "Scheduled tab: pending • schedule_at 09:00 WAT (stored UTC 08:00)"],
        ["08:59", "Inline poller (30s) + Beat (1m) both check", "Still pending"],
        ["09:00:15", "Earliest poller wins atomic claim → validates → populates contacts → enqueues", "Scheduled→ Running → Messages queued"],
        ["09:00:30", "Gateway sends → provider ID returned", "Message bubble green “sent” in Inbox; Scheduled status: sent"],
        ["09:01–09:03", "Webhook sms:sent / delivered arrives", "Ticks: ✓ → ✓✓ blue; Analytics delivery +1"],
        ["09:00 if offline", "Gateway rejects: device offline / auth", "Scheduled→ failed + error ‘Device offline’. Inbox red bubble. Failed tab + Retry button"],
    ]
    story.append(make_table([[Paragraph(c, s_table_header if i==0 else s_table_cell) for c in r] for i,r in enumerate(timeline)], [25*mm, 60*mm, 85*mm]))
    story.append(p("Failed tab is two sources merged:", s_h3))
    story.append(bullet("ScheduledMessage failed (never reached gateway: opt-out, suppression, offline) — stored with error text. Retry re-queues for next poll (now +0 s)."))
    story.append(bullet("Message failed (reached gateway but rejected: “Timed out”, “No credentials”, bad number) — live Message row with last_error + failed_at. Retry calls gateway again immediately."))
    story.append(p("You can <b>cancel</b> a pending scheduled (only while pending). After it fires you must <b>retry</b> rather than cancel — history is immutable for audit.", s_small))
    story.append(p("<b>Do this now:</b> Schedule a message for +2 min, leave this guide open, watch Scheduled → Sent live without refresh (polls every 15s).", s_quote))

    # 17 Best Practices
    story.append(p("17 &nbsp; Best Practices &amp; Compliance — Stay loved, not blocked", s_h1))
    story.append(HRFlowable(width="30%", thickness=2, color=COLORS["primary"], spaceAfter=8, hAlign='LEFT'))
    story.append(p("SMS is permission marketing. One angry STOP can cost you. Nigerian NCC + carrier filters punish spam quickly.", s_body))
    story.append(p("Timing", s_h2))
    for b in [
        "Restaurants: <b>11 AM (lunch) & 5 PM (dinner)</b> get best open. Avoid 8 PM–7 AM — people sleep.",
        "Weekends: lunch promos Sat 11 AM beat Sunday 9 AM.",
        "Spacing: <b>max 2 promos/week</b> per list. More = opt-out spike.",
        "Holidays: send <b>day before</b> (people plan), not day-of chaos.",
    ]:
        story.append(bullet(b))
    story.append(p("Copy that converts", s_h2))
    for b in [
        "Front-load value: “20% OFF Family Feast — today only” in first 30 chars (preview).",
        "One clear CTA: “Reply MENU”, “Show this SMS”, “Call 0700…”. Not three.",
        "Emoji: 1 max for restaurants (🍗 🍕). More triggers UCS-2 (70-char limit) and looks spammy.",
        "Always end with <b>“Reply STOP to opt out”</b> — builds trust + legal.",
        "Personalize at least {{first_name}} or {{city}} — lifts reply 25%.",
    ]:
        story.append(bullet(b))
    story.append(p("Compliance checklist", s_h2))
    for b in [
        "Consent: Only message numbers who gave you their card / filled form / ordered online (and didn’t untick).",
        "STOP honored instantly — we auto-opt-out and suppress; never re-add manually.",
        "ID: Sign SMS with brand name (“— Chicken Republic”) so they know who you are.",
        "Frequency: track opt-out rate per blast; >2% = you over-message.",
        "Records: keep suppression CSV export for 2 years.",
    ]:
        story.append(bullet(b))
    cost_tbl = [
        ["Length", "Chars", "Segments", "Cost (approx)"],
        ["Single", "1–160 (GSM-7)", "1", "₦4"],
        ["Single Unicode", "1–70", "1", "₦4"],
        ["Multi", "161–306", "2", "₦8"],
        ["Long", "307–459", "3", "₦12"],
    ]
    story.append(p("Cost (NGN) — example per segment:", s_h3))
    story.append(make_table([[Paragraph(c, s_table_header if i==0 else s_table_cell) for c in r] for i,r in enumerate(cost_tbl)], [30*mm, 45*mm, 30*mm, 65*mm]))
    story.append(p("Unicode trap: One curly quote ’ or emoji drops limit 160→70. Test with “Preview” counter — if it says “unicode (70/SMS)” trim emoji/curly.", s_small))

    # 18 Troubleshooting
    story.append(p("18 &nbsp; Troubleshooting &amp; FAQ", s_h1))
    story.append(HRFlowable(width="30%", thickness=2, color=COLORS["primary"], spaceAfter=8, hAlign='LEFT'))
    faq = [
        ["“Campaign stuck at 0 sent”", "• Gateway credentials wrong? Settings → Test → should be Healthy.\n• List empty? Campaign validation would have blocked — check list member count.\n• Check CampaignContacts: pending? → worker sleeping: Inbox Sync also launches due campaigns."],
        ["“Scheduled never sent”", "• Was schedule_at in the future (UTC)? We now auto-convert via toISOString; old +01:00 bug fixed.\n• Look in Scheduled → Pending: still there? Check error tab. Device offline shows there.\n• Inline poller runs every 30s even without Celery — but if ENABLE_INLINE_POLLER=false, you need beat."],
        ["“All sends show Failed”", "• Tap Failed → read error. Common: “No credentials”, “Timed out” (phone offline), “Invalid number”.\n• Check SMSGATE_USERNAME/PASSWORD vs Cloud Server creds (not website login).\n• On mobile: open SMS-Gate app → verify device shows Online."],
        ["“Inbox empty after Sync”", "• PUBLIC_BASE_URL not set → gateway has nowhere to POST → Sync triggers but nothing lands → Debug shows “No webhook”.\n• Fix env, redeploy, re-Sync, wait 10s → webhooks land async."],
        ["“Ticks stuck on single ✓”", "• Sent but not delivered — carrier delay. Poll every 3 min updates via poll_status_for_ids.\n• If never delivers after hours, number may be off or DND."],
        ["“Import skipped many”", "• “Duplicates” = phone already exists (we store +234). Check Invalid = malformed. Copy errors preview."],
        ["“Can’t edit running campaign?”", "• Frozen for audit: running/paused/completed can’t change message/list. Duplicate it (Copy) and edit copy."],
        ["“Message shows STOP but didn’t opt out?”", "• Only inbound STOP opts out. Outbound “STOP to opt out” is just text. Inbound detection is case-insensitive."],
    ]
    for q,a in faq:
        story.append(p(f"<b>Q: {q}</b>", s_h3))
        story.append(p(a.replace("\n","<br/>"), s_body))
        story.append(Spacer(1,1*mm))
    story.append(p('<b>Need more?</b> In Inbox tap bug icon → “Full diagnostic” copies all checks + last 10 webhooks for support.', s_quote))

    # 19 Glossary
    story.append(p("19 &nbsp; Glossary &amp; Support", s_h1))
    story.append(HRFlowable(width="30%", thickness=2, color=COLORS["primary"], spaceAfter=8, hAlign='LEFT'))
    gloss = [
        ["Campaign", "Blast to a list; own message + stats."],
        ["CampaignContact", "One row per (campaign, contact) with status pending→sent/delivered/failed/replied."],
        ["ScheduledMessage", "One-off timed SMS from Send page; pending→sent/failed/cancelled; becomes Message when fired."],
        ["Message", "Single bubble in a Conversation (direction incoming/outgoing, status queued→sent→delivered/failed)."],
        ["Conversation", "One thread per contact (Unique contact_id); shows unread count, last preview."],
        ["Template", "Reusable body with {{variables}}; versioned via sequence_version snapshot if used in campaign."],
        ["Sequence / FollowUp", "Drip automation; FollowUp is the scheduled next step with scheduled_at."],
        ["Gateway", "SMS-Gate provider; credentials + webhook secret + deviceId."],
        ["Suppression", "Global blocklist for STOP/opt-out; checked before every send."],
        ["Idempotency Key", "Unique per message to dedupe webhook replays (e.g. inbound-V2)."],
    ]
    story.append(make_table([[Paragraph(c, s_table_header if i==0 else s_table_cell) for c in r] for i,r in enumerate([["Term","Meaning"]] + gloss)], [45*mm, 125*mm]))
    story.append(Spacer(1,4*mm))
    support_tbl = [
        ["Channel", "How", "When"],
        ["In-app Help", "Settings → Support / Docs", "Always"],
        ["Email", "hello@sendsms.ng", "9 AM–6 PM WAT"],
        ["Phone", "Via gateway SIM (your device)", "For gateway troubleshooting"],
    ]
    story.append(make_table([[Paragraph(c, s_table_header if i==0 else s_table_cell) for c in r] for i,r in enumerate(support_tbl)], [35*mm, 75*mm, 60*mm]))
    story.append(Spacer(1,6*mm))
    story.append(p('<b>You’re ready.</b> Import your restaurant contacts, copy one template, schedule for tomorrow 11 AM, and reply like WhatsApp. Your guests are hungry — tell them what’s cooking. 🍗🍕', ParagraphStyle('final', parent=s_body, alignment=TA_CENTER, fontSize=11, leading=15, textColor=COLORS["primary_dark"], fontName='Helvetica-Bold', backColor=HexColor("#f0f9f6"), borderPadding=(12,12,12,12), borderColor=COLORS["primary"], borderWidth=0.5)))
    story.append(Spacer(1,6*mm))
    story.append(p(f"Guide generated {datetime.now().strftime('%d %B %Y')} • SENDERSMS v2.1 • For internal restaurant partner use. Prices & carrier rules subject to change.", ParagraphStyle('footer', parent=s_small, alignment=TA_CENTER, fontSize=7)))

    doc.build(story, onFirstPage=cover_header_footer, onLaterPages=header_footer)
    print(f"PDF built: {output_path}")

if __name__ == "__main__":
    os.makedirs("docs/images", exist_ok=True)
    out = "SENDERSMS_Restaurant_Guide.pdf"
    # also keep copy under docs/
    build_pdf(out)
    # copy to docs
    import shutil
    shutil.copy(out, "docs/SENDERSMS_Restaurant_Guide.pdf")
    print("Done - also copied to docs/")

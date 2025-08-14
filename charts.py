import os
import streamlit as st

def kpi_card_md(title, value, color="#2563eb", subtext=""):
    """
    Returns a small HTML block for a KPI card.
    """
    try:
        if isinstance(value, float) and abs(value) < 1e6:
            vstr = f"{value:,.1f}"
        else:
            vstr = f"{value:,.0f}"
    except Exception:
        vstr = str(value)

    html = f"""
    <div style="background:#f9fafb;border-radius:12px;padding:16px;
                box-shadow:0 2px 6px rgba(0,0,0,.06);text-align:center;">
      <div style="font-size:12px;color:#64748b;margin-bottom:6px;">{title}</div>
      <div style="font-size:22px;font-weight:700;color:{color};line-height:1;">{vstr}</div>
      <div style="font-size:12px;color:#6b7280;margin-top:4px;">{subtext}</div>
    </div>
    """
    return html

def inject_watermark(stmod, image_path=None):
    """
    Shows a small logo in the sidebar (bottom). If no image found, shows nothing.
    """
    with stmod.sidebar:
        stmod.markdown("---")
        p = None
        if image_path:
            if os.path.exists(image_path):
                p = image_path
            else:
                # also check relative to working dir
                if os.path.exists(os.path.join(os.getcwd(), image_path)):
                    p = os.path.join(os.getcwd(), image_path)
        if p:
            stmod.image(p, caption="",  use_container_width=True)
        else:
            stmod.caption("")

# (Optional placeholders to satisfy imports if you later add them)
def donut(*args, **kwargs):
    return None

def line_two(*args, **kwargs):
    return None

def waterfall_from_monthly(*args, **kwargs):
    return None

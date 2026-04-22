"""
TOTVS to RD Station Deal Sync

Fetches deals/sales orders from TOTVS ERP API and creates corresponding
deals in RD Station CRM. Handles organization lookup, contact creation,
product mapping, and deal creation with automatic duplicate detection.

Designed to be a bridge between TOTVS ERP and RD Station CRM.
"""

import os
import re
import json
import unicodedata
import smtplib
import requests
import gspread
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from oauth2client.service_account import ServiceAccountCredentials
from rd_token import get_access_token


# ========= CONFIGURATION ==========
# RD Station
RD_API = "https://api.rd.services/crm/v2"
RD_OWNER_ID = os.getenv("RD_OWNER_ID")  # RD Station salesperson ID
RD_STAGE_ID = os.getenv("RD_STAGE_ID")  # Pipeline stage ID

# TOTVS ERP
TOTVS_BASE_URL = os.getenv("TOTVS_BASE_URL")  # e.g., https://api.totvs.com/erp
TOTVS_API_KEY = os.getenv("TOTVS_API_KEY")
TOTVS_COMPANY_ID = os.getenv("TOTVS_COMPANY_ID")

# RD Station Custom Field (where you store "Faturado por" info)
RD_CUSTOM_FIELD_BILLED_BY = os.getenv("RD_CUSTOM_FIELD_BILLED_BY", "faturado_por")

# Product mapping: TOTVS product name → RD Station product ID
# Override in environment or edit here
PRODUCT_MAP = {
    "agro": os.getenv("RD_PRODUCT_AGRO"),
    "infrastructure": os.getenv("RD_PRODUCT_INFRASTRUCTURE"),
    "housing": os.getenv("RD_PRODUCT_HOUSING"),
    "retail": os.getenv("RD_PRODUCT_RETAIL"),
}

DEFAULT_PRODUCT_ID = os.getenv("RD_DEFAULT_PRODUCT_ID")

# Google Sheets (for duplicate tracking)
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
GOOGLE_DUPLICATE_SHEET = os.getenv("GOOGLE_DUPLICATE_SHEET", "TOTVS_SYNCED_DEALS")

# Email
RECIPIENT_EMAIL = os.getenv("RECIPIENT_EMAIL")
TEST_MODE = os.getenv("TEST_MODE", "False").lower() == "true"
TEST_EMAIL = os.getenv("TEST_EMAIL")


# ========= HELPERS ==========
def normalize_for_match(text):
    """Remove accents, special chars, lowercase for fuzzy matching."""
    if not text:
        return ""
    text = unicodedata.normalize('NFKD', str(text))
    text = ''.join([c for c in text if not unicodedata.combining(c)])
    text = text.lower()
    text = re.sub(r'[^a-z0-9\s]', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def normalize_text(v):
    """Simple lowercase normalization."""
    return str(v).strip().lower()


def normalize_value(v):
    """Convert currency string to float-compatible format."""
    return str(v).replace(".", "").replace(",", ".").strip()


def get_rd_headers(token):
    """Standard headers for RD Station API requests."""
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }


def get_totvs_headers():
    """Standard headers for TOTVS API requests."""
    return {
        "Authorization": f"Bearer {TOTVS_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }


# ========= GOOGLE SHEETS CLIENT ==========
def get_gspread_client():
    """Authenticate with Google Sheets API."""
    if not GOOGLE_SHEET_ID:
        return None
    try:
        creds = json.loads(os.getenv("GOOGLE_CREDENTIALS", "{}"))
        return gspread.authorize(
            ServiceAccountCredentials.from_json_keyfile_dict(creds)
        )
    except Exception as e:
        print(f"⚠ Google Sheets auth failed: {e}")
        return None


# ========= FETCH FROM TOTVS ==========
def get_deals_from_totvs():
    """
    Fetch sales orders/deals from TOTVS ERP API.
    
    Expected response format (customize based on your TOTVS API):
    {
        "data": [
            {
                "id": "order_id",
                "order_number": "SO-001",
                "customer_name": "Company Name",
                "customer_email": "contact@company.com",
                "contact_name": "John Doe",
                "product_name": "agro",
                "amount": "1500.50",
                "salesperson": "Sales Rep Name",
                "status": "open"
            },
            ...
        ]
    }
    """
    if not TOTVS_BASE_URL or not TOTVS_API_KEY:
        raise RuntimeError("TOTVS_BASE_URL and TOTVS_API_KEY must be set")
    
    url = f"{TOTVS_BASE_URL}/sales-orders"
    params = {
        "company_id": TOTVS_COMPANY_ID,
        "status": "open",  # Only open orders
        "page": 1,
        "limit": 100
    }
    
    headers = get_totvs_headers()
    all_deals = []
    
    while True:
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            
            orders = data.get("data", [])
            if not orders:
                break
            
            all_deals.extend(orders)
            
            # Check for pagination
            if not data.get("pagination", {}).get("has_next"):
                break
            
            params["page"] += 1
        except requests.RequestException as e:
            print(f"❌ TOTVS API error: {e}")
            raise
    
    print(f"✓ Fetched {len(all_deals)} deals from TOTVS")
    return all_deals


# ========= RD STATION ORGANIZATION LOOKUP ==========
def get_all_organizations(access_token):
    """
    Fetch all organizations from RD Station.
    
    Returns dict mapping normalized company names to org IDs.
    """
    headers = get_rd_headers(access_token)
    org_map = {}
    raw_map = {}
    page = 1
    
    while True:
        params = {
            "page[number]": page,
            "page[size]": 100,
            "sort[name]": "asc"
        }
        
        try:
            resp = requests.get(
                f"{RD_API}/organizations",
                headers=headers,
                params=params,
                timeout=30
            )
            
            if resp.status_code == 401:
                print("  Token expired, refreshing...")
                access_token = get_access_token()
                headers = get_rd_headers(access_token)
                resp = requests.get(
                    f"{RD_API}/organizations",
                    headers=headers,
                    params=params,
                    timeout=30
                )
            
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"  Error fetching organizations: {e}")
            break
        
        payload = resp.json()
        data = payload.get("data", [])
        
        if not data:
            break
        
        for org in data:
            org_id = org["id"]
            raw_name = org.get("name", "")
            
            # Normalized match (best)
            norm_name = normalize_for_match(raw_name)
            if norm_name:
                org_map[norm_name] = org_id
            
            # Raw lowercase match (fallback)
            raw_lower = normalize_text(raw_name)
            if raw_lower:
                raw_map[raw_lower] = org_id
        
        if not payload.get("links", {}).get("next"):
            break
        
        page += 1
    
    print(f"✓ Fetched {len(org_map)} organizations from RD Station")
    org_map.update(raw_map)
    return org_map


# ========= DUPLICATE DETECTION ==========
def build_duplicate_index(gc):
    """
    Build index of already-synced deals from Google Sheets.
    
    Prevents creating duplicate deals on multiple runs.
    """
    if not gc or not GOOGLE_SHEET_ID:
        print("⚠ Google Sheets not configured, duplicate detection disabled")
        return set()
    
    try:
        sheet = gc.open_by_key(GOOGLE_SHEET_ID).worksheet(GOOGLE_DUPLICATE_SHEET)
        rows = sheet.get_all_values()
        
        index = set()
        for row in rows[1:]:  # Skip header
            if len(row) < 5:
                continue
            
            totvs_id = row[0]
            customer_name = normalize_text(row[1])
            amount = normalize_value(row[2])
            
            # Create composite key for duplicate detection
            key = f"{totvs_id}|{customer_name}|{amount}"
            if key:
                index.add(key)
        
        print(f"✓ Built duplicate index: {len(index)} entries")
        return index
    except Exception as e:
        print(f"⚠ Could not build duplicate index: {e}")
        return set()


def add_to_duplicate_index(gc, totvs_deal):
    """Add synced deal to Google Sheets duplicate index."""
    if not gc or not GOOGLE_SHEET_ID:
        return
    
    try:
        sheet = gc.open_by_key(GOOGLE_SHEET_ID).worksheet(GOOGLE_DUPLICATE_SHEET)
        sheet.append_row([
            totvs_deal.get("id"),
            totvs_deal.get("customer_name"),
            totvs_deal.get("amount"),
            datetime.now().isoformat(),
            "SYNCED"
        ])
    except Exception as e:
        print(f"⚠ Could not update duplicate index: {e}")


# ========= RD STATION OPERATIONS ==========
def get_or_create_contact(name, email, org_id, company_name, token):
    """
    Get existing contact or create new one in RD Station.
    
    Args:
        name: Contact name
        email: Contact email
        org_id: RD Station organization ID
        company_name: Fallback if name is invalid
        token: RD Station access token
    
    Returns:
        Contact ID
    """
    if not name or len(name.strip()) < 2:
        fallback = f"Contact {company_name}" if company_name else "Main Contact"
        print(f"  ⚠ Invalid name, using fallback: '{fallback}'")
        name = fallback
    
    # Try to find by email
    if email:
        try:
            resp = requests.get(
                f"{RD_API}/contacts",
                headers=get_rd_headers(token),
                params={"email": email},
                timeout=30
            )
            data = resp.json().get("data", [])
            for contact in data:
                if normalize_text(contact.get("email", "")) == normalize_text(email):
                    print(f"  ✓ Contact exists: {contact['id']}")
                    return contact["id"]
        except Exception as e:
            print(f"  ⚠ Email lookup failed: {e}")
    
    # Create new contact
    payload = {
        "data": {
            "name": name,
            "email": email or f"{name.replace(' ', '').lower()}@placeholder.com",
            "organization_id": org_id
        }
    }
    
    try:
        resp = requests.post(
            f"{RD_API}/contacts",
            headers=get_rd_headers(token),
            json=payload,
            timeout=30
        )
        resp.raise_for_status()
        contact_id = resp.json()["data"]["id"]
        print(f"  ✓ Contact created: {contact_id}")
        return contact_id
    except Exception as e:
        raise RuntimeError(f"Failed to create contact: {e}")


def create_deal_with_product(name, amount, org_id, contact_id, billed_by, product_id, token):
    """
    Create deal in RD Station and attach product.
    
    Args:
        name: Deal name
        amount: Deal value
        org_id: Organization ID
        contact_id: Contact ID
        billed_by: Custom field value (e.g., salesperson name)
        product_id: RD Station product ID
        token: Access token
    
    Returns:
        True if successful, False otherwise
    """
    payload = {
        "data": {
            "name": name,
            "owner_id": RD_OWNER_ID,
            "stage_id": RD_STAGE_ID,
            "status": "ongoing",
            "organization_id": org_id,
            "contact_ids": [contact_id],
            "custom_fields": {
                RD_CUSTOM_FIELD_BILLED_BY: billed_by
            }
        }
    }
    
    try:
        resp = requests.post(
            f"{RD_API}/deals",
            headers=get_rd_headers(token),
            json=payload,
            timeout=30
        )
        resp.raise_for_status()
        deal_id = resp.json()["data"]["id"]
        print(f"  ✓ Deal created: {deal_id}")
    except Exception as e:
        print(f"  ❌ Deal creation failed: {e}")
        return False
    
    # Attach product
    product_payload = {
        "data": {
            "product_id": product_id,
            "quantity": 1,
            "price": float(amount),
            "discount": 0,
            "discount_type": "percentage"
        }
    }
    
    try:
        resp = requests.post(
            f"{RD_API}/deals/{deal_id}/products",
            headers=get_rd_headers(token),
            json=product_payload,
            timeout=30
        )
        resp.raise_for_status()
        print(f"  ✓ Product attached")
        return True
    except Exception as e:
        print(f"  ⚠ Product attachment failed: {e}")
        return False


# ========= EMAIL ==========
def send_email_html(to_email, subject, html_body):
    """Send HTML email via SMTP."""
    user = os.getenv("EMAIL_USER")
    password = os.getenv("EMAIL_PASS")
    smtp_host = os.getenv("EMAIL_SMTP_HOST", "smtp.office365.com")
    smtp_port = int(os.getenv("EMAIL_SMTP_PORT", "587"))
    
    if not user or not password:
        print("⚠ Email credentials missing, skipping email")
        return
    
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to_email
    msg.attach(MIMEText(html_body, "html"))
    
    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(user, password)
            server.sendmail(user, [to_email], msg.as_string())
        print(f"✓ Email sent to {to_email}")
    except Exception as e:
        print(f"❌ Email failed: {e}")


def build_summary_html(created_deals, skipped_deals):
    """Build HTML summary email."""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Created deals
    created_rows = ""
    for d in created_deals:
        created_rows += f"""
            <tr style="background-color:#ffffff;">
                <td style="padding:8px;">{d['totvs_id']}</td>
                <td style="padding:8px;">{d['deal_name']}</td>
                <td style="padding:8px;">{d['customer']}</td>
                <td style="padding:8px;">{d['amount']}</td>
            </tr>
        """
    if not created_rows:
        created_rows = "<tr><td colspan='4' style='padding:8px;'>No deals created.</td></tr>"
    
    # Skipped deals
    skipped_rows = ""
    for d in skipped_deals:
        skipped_rows += f"""
            <tr style="background-color:#ffffff;">
                <td style="padding:8px;">{d['totvs_id']}</td>
                <td style="padding:8px;">{d['deal_name']}</td>
                <td style="padding:8px;">{d['customer']}</td>
                <td style="padding:8px; color:#D64550;">{d['reason']}</td>
            </tr>
        """
    if not skipped_rows:
        skipped_rows = "<tr><td colspan='4' style='padding:8px;'>No deals skipped.</td></tr>"
    
    html = f"""
    <html>
    <body style="font-family:Arial; background:#f7f7f7; padding:20px;">
        <div style="background:#007bff; padding:18px; color:white; font-size:22px; font-weight:bold; text-align:center; border-radius:6px; margin-bottom:20px;">
            📊 TOTVS to RD Station Sync Report
        </div>
        <div style="background:white; padding:20px; border-radius:6px; box-shadow:0 2px 4px rgba(0,0,0,0.1);">
            <p style="font-size:15px; color:#333;">
                Sync executed at: <strong>{now_str}</strong><br>
                <strong>Created:</strong> {len(created_deals)} | <strong>Skipped:</strong> {len(skipped_deals)}
            </p>
            
            <h3 style="color:#007bff; border-bottom:2px solid #007bff; padding-bottom:10px;">✅ Created Deals</h3>
            <table cellpadding="0" cellspacing="0" width="100%" style="border-collapse:collapse;">
                <thead>
                    <tr style="background-color:#007bff; color:white;">
                        <th style="padding:10px; text-align:left;">TOTVS ID</th>
                        <th style="padding:10px; text-align:left;">Deal Name</th>
                        <th style="padding:10px; text-align:left;">Customer</th>
                        <th style="padding:10px; text-align:left;">Amount</th>
                    </tr>
                </thead>
                <tbody>
                    {created_rows}
                </tbody>
            </table>
            
            <h3 style="color:#D64550; border-bottom:2px solid #D64550; padding-bottom:10px; margin-top:20px;">❌ Skipped Deals</h3>
            <table cellpadding="0" cellspacing="0" width="100%" style="border-collapse:collapse;">
                <thead>
                    <tr style="background-color:#D64550; color:white;">
                        <th style="padding:10px; text-align:left;">TOTVS ID</th>
                        <th style="padding:10px; text-align:left;">Deal Name</th>
                        <th style="padding:10px; text-align:left;">Customer</th>
                        <th style="padding:10px; text-align:left;">Reason</th>
                    </tr>
                </thead>
                <tbody>
                    {skipped_rows}
                </tbody>
            </table>
            
            <div style="margin-top:25px; padding-top:15px; border-top:1px solid #eee; text-align:center; color:#888; font-size:12px;">
                <strong>TOTVS to RD Station Integration</strong>
            </div>
        </div>
    </body>
    </html>
    """
    return html


def send_summary_email(created_deals, skipped_deals):
    """Send summary email to recipient."""
    recipient = TEST_EMAIL if TEST_MODE else RECIPIENT_EMAIL
    
    if not recipient:
        print("⚠ No recipient configured, skipping email")
        return
    
    if TEST_MODE:
        print(f"🧪 TEST MODE: Email would be sent to {recipient}")
    
    subject = f"TOTVS Sync Report – {len(created_deals)} created, {len(skipped_deals)} skipped"
    html_body = build_summary_html(created_deals, skipped_deals)
    send_email_html(recipient, subject, html_body)


# ========= MAIN ==========
def main():
    """Main sync pipeline."""
    print("=" * 60)
    print("TOTVS to RD Station Deal Sync — Starting")
    print("=" * 60)
    
    try:
        # Get RD Station token
        print("\n1️⃣ Getting RD Station access token...")
        rd_token = get_access_token()
        
        # Fetch deals from TOTVS
        print("\n2️⃣ Fetching deals from TOTVS...")
        totvs_deals = get_deals_from_totvs()
        
        if not totvs_deals:
            print("⚠ No deals found in TOTVS")
            return
        
        # Fetch RD Station organizations
        print("\n3️⃣ Fetching organizations from RD Station...")
        org_map = get_all_organizations(rd_token)
        
        # Build duplicate index
        print("\n4️⃣ Building duplicate detection index...")
        gc = get_gspread_client()
        duplicate_index = build_duplicate_index(gc)
        
        # Process each deal
        print("\n5️⃣ Processing deals...\n")
        created_deals = []
        skipped_deals = []
        
        for deal in totvs_deals:
            try:
                totvs_id = deal.get("id")
                deal_name = deal.get("order_number", "Unknown")
                customer_name = deal.get("customer_name", "")
                customer_email = deal.get("customer_email", "")
                contact_name = deal.get("contact_name", customer_name)
                product_name = deal.get("product_name", "")
                amount = normalize_value(deal.get("amount", "0"))
                salesperson = deal.get("salesperson", "")
                
                # Check duplicates
                dup_key = f"{totvs_id}|{normalize_text(customer_name)}|{amount}"
                if dup_key in duplicate_index:
                    print(f"⏭️  Skipping (duplicate): {deal_name}")
                    skipped_deals.append({
                        "totvs_id": totvs_id,
                        "deal_name": deal_name,
                        "customer": customer_name,
                        "reason": "Already synced"
                    })
                    continue
                
                print(f"Processing: {deal_name}")
                
                # Find organization
                norm_customer = normalize_for_match(customer_name)
                org_id = org_map.get(norm_customer)
                
                if not org_id:
                    org_id = org_map.get(normalize_text(customer_name))
                
                if not org_id:
                    # Partial match
                    for norm_org_name, oid in org_map.items():
                        if norm_customer in norm_org_name:
                            org_id = oid
                            print(f"  Partial match: {customer_name}")
                            break
                
                if not org_id:
                    print(f"  ❌ Organization not found: {customer_name}")
                    skipped_deals.append({
                        "totvs_id": totvs_id,
                        "deal_name": deal_name,
                        "customer": customer_name,
                        "reason": f"Organization not found"
                    })
                    continue
                
                # Find product
                product_id = PRODUCT_MAP.get(normalize_text(product_name))
                if not product_id:
                    if DEFAULT_PRODUCT_ID:
                        print(f"  ⚠ Using default product for: {product_name}")
                        product_id = DEFAULT_PRODUCT_ID
                    else:
                        print(f"  ❌ Product not found: {product_name}")
                        skipped_deals.append({
                            "totvs_id": totvs_id,
                            "deal_name": deal_name,
                            "customer": customer_name,
                            "reason": f"Product '{product_name}' not mapped"
                        })
                        continue
                
                # Get/create contact
                contact_id = get_or_create_contact(contact_name, customer_email, org_id, customer_name, rd_token)
                
                # Create deal
                ok = create_deal_with_product(deal_name, amount, org_id, contact_id, salesperson, product_id, rd_token)
                
                if ok:
                    created_deals.append({
                        "totvs_id": totvs_id,
                        "deal_name": deal_name,
                        "customer": customer_name,
                        "amount": deal.get("amount", "")
                    })
                    
                    # Add to duplicate index
                    add_to_duplicate_index(gc, deal)
                else:
                    skipped_deals.append({
                        "totvs_id": totvs_id,
                        "deal_name": deal_name,
                        "customer": customer_name,
                        "reason": "Failed to create deal"
                    })
            
            except Exception as e:
                print(f"❌ Error processing deal: {e}")
                skipped_deals.append({
                    "totvs_id": deal.get("id", "Unknown"),
                    "deal_name": deal.get("order_number", "Unknown"),
                    "customer": deal.get("customer_name", "Unknown"),
                    "reason": f"Exception: {str(e)[:50]}"
                })
        
        # Summary
        print("\n" + "=" * 60)
        print(f"✓ Sync complete: {len(created_deals)} created, {len(skipped_deals)} skipped")
        print("=" * 60)
        
        # Send email
        send_summary_email(created_deals, skipped_deals)
    
    except Exception as e:
        print(f"\n❌ FATAL ERROR: {e}")
        raise


if __name__ == "__main__":
    main()

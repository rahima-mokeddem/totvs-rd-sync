# totvs-rd-sync

🔄 Sync sales orders from TOTVS ERP to RD Station CRM.

Automatically fetches open sales orders from TOTVS and creates corresponding deals in RD Station. Handles organization lookup, contact creation, product mapping, and duplicate detection.

## Features

📦 **TOTVS Integration**
- Fetches open sales orders from TOTVS ERP API
- Maps TOTVS data to RD Station fields
- Handles pagination automatically

🤝 **Smart Organization Matching**
- Exact name matching (normalized)
- Fallback to fuzzy matching
- Creates missing contacts automatically

🔄 **Duplicate Prevention**
- Tracks synced deals in Google Sheets
- Prevents duplicate deal creation
- Composite key matching (ID + customer + amount)

📊 **Product Mapping**
- Configurable product name → RD Station ID mapping
- Fallback to default product
- Customizable via environment variables

📧 **Email Reports**
- HTML summary email after each sync
- Lists created and skipped deals
- Optional test mode for dry runs

## Installation

```bash
git clone https://github.com/yourusername/totvs-rd-sync.git
cd totvs-rd-sync
pip install -r requirements.txt
```

## Quick Start

### 1. Set Environment Variables

```bash
# TOTVS ERP
export TOTVS_BASE_URL="https://api.totvs.com/erp"
export TOTVS_API_KEY="your_totvs_api_key"
export TOTVS_COMPANY_ID="your_company_id"

# RD Station
export RD_OWNER_ID="owner_id_from_rd"
export RD_STAGE_ID="stage_id_from_rd"
export RD_CLIENT_ID="your_client_id"
export RD_CLIENT_SECRET="your_client_secret"
export REFRESH_TOKEN="your_refresh_token"

# Product Mapping
export RD_PRODUCT_AGRO="product_id_1"
export RD_PRODUCT_INFRASTRUCTURE="product_id_2"
export RD_PRODUCT_HOUSING="product_id_3"
export RD_PRODUCT_RETAIL="product_id_4"
export RD_DEFAULT_PRODUCT_ID="fallback_product_id"

# Google Sheets (for duplicate tracking)
export GOOGLE_SHEET_ID="your_spreadsheet_id"
export GOOGLE_CREDENTIALS='{"type":"service_account",...}'

# Email
export RECIPIENT_EMAIL="your-email@example.com"
export EMAIL_USER="sender-email@gmail.com"
export EMAIL_PASS="your_app_password"
export EMAIL_SMTP_HOST="smtp.gmail.com"
export EMAIL_SMTP_PORT="587"
```

Or create a `.env` file (see `example.env`):

```bash
cp example.env .env
# Edit .env with your values
```

### 2. Initialize RD Token (First Time Only)

Get initial refresh token from RD Station and seed it:

```bash
python -c "from rd_token import seed_initial_token; seed_initial_token('YOUR_TOKEN')"
```

### 3. Create Google Sheets Duplicate Index

Create a spreadsheet with a sheet named `TOTVS_SYNCED_DEALS` with columns:
- TOTVS ID
- Customer Name
- Amount
- Sync Date
- Status

### 4. Run the Sync

```bash
python totvs_rd_sync.py
```

## How It Works

```
TOTVS ERP
    ↓
Fetch open sales orders
    ↓
Match to RD Station organizations
    ↓
Check for duplicates
    ↓
Create/get contacts
    ↓
Create deals + attach products
    ↓
Send summary email
    ↓
RD Station
```

## Configuration

### Environment Variables

**Required**:
```bash
# TOTVS
TOTVS_BASE_URL=https://api.totvs.com/erp
TOTVS_API_KEY=your_key
TOTVS_COMPANY_ID=your_company

# RD Station
RD_OWNER_ID=owner_id
RD_STAGE_ID=stage_id
RD_CLIENT_ID=client_id
RD_CLIENT_SECRET=client_secret
REFRESH_TOKEN=refresh_token

# Email
RECIPIENT_EMAIL=your@email.com
EMAIL_USER=sender@email.com
EMAIL_PASS=app_password
```

**Optional**:
```bash
# Google Sheets (duplicate detection)
GOOGLE_SHEET_ID=spreadsheet_id
GOOGLE_CREDENTIALS=json_key

# Test mode
TEST_MODE=true
TEST_EMAIL=test@email.com

# Product mapping
RD_PRODUCT_AGRO=id1
RD_PRODUCT_INFRASTRUCTURE=id2
RD_PRODUCT_HOUSING=id3
RD_PRODUCT_RETAIL=id4
RD_DEFAULT_PRODUCT_ID=default_id

# Custom fields
RD_CUSTOM_FIELD_BILLED_BY=field_name
```

### TOTVS API Response Format

The script expects TOTVS to return deals in this format:

```json
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
    }
  ],
  "pagination": {
    "has_next": false
  }
}
```

**Customize the `get_deals_from_totvs()` function if your API format differs.**

## Examples

### Run Sync

```bash
python totvs_rd_sync.py
```

### Test Mode (Dry Run)

```bash
TEST_MODE=true python totvs_rd_sync.py
```

### Custom Product Mapping

```bash
export RD_PRODUCT_AGRO="12345"
export RD_PRODUCT_INFRASTRUCTURE="67890"
python totvs_rd_sync.py
```

## Troubleshooting

### "TOTVS API error"

**Cause**: Invalid credentials or API URL

**Fix**:
- Verify `TOTVS_BASE_URL`, `TOTVS_API_KEY`, `TOTVS_COMPANY_ID`
- Check TOTVS API documentation for correct endpoint format

### "No organizations found in RD Station"

**Cause**: Token issues or wrong credentials

**Fix**:
- Verify `RD_CLIENT_ID` and `RD_CLIENT_SECRET`
- Refresh token: `python -c "from rd_token import seed_initial_token; seed_initial_token('YOUR_TOKEN')"`

### "Organization not found"

**Cause**: Customer name in TOTVS doesn't match RD Station org name

**Fix**:
- Check spelling in both systems
- Add manual entry to RD Station if needed
- Implement custom matching logic in `get_all_organizations()`

### "Product not found"

**Cause**: Product name doesn't match mapping

**Fix**:
- Check product name spelling in TOTVS
- Add to `PRODUCT_MAP` or set `RD_DEFAULT_PRODUCT_ID`
- Customize product lookup in code

### Duplicate deals not being detected

**Cause**: Google Sheets not configured or formula issues

**Fix**:
- Set `GOOGLE_SHEET_ID` and `GOOGLE_CREDENTIALS`
- Verify `TOTVS_SYNCED_DEALS` sheet exists
- Check sheet format matches expected columns

## Architecture

### Why This Approach?

TOTVS and RD Station use different data models:
- TOTVS: Sales orders, inventory, invoicing
- RD Station: Sales pipeline, deal stages, contacts

This sync bridges the gap by:
- Fetching transactional data from TOTVS
- Mapping to RD Station deal structure
- Maintaining duplicate-free index in Google Sheets

### Customization Points

- **TOTVS data extraction**: Modify `get_deals_from_totvs()`
- **Organization matching**: Adjust `get_all_organizations()`
- **Product mapping**: Update `PRODUCT_MAP` or `get_deals_from_totvs()`
- **Deal fields**: Customize payload in `create_deal_with_product()`
- **Email format**: Edit `build_summary_html()`

## Dependencies

- `requests` — HTTP requests to APIs
- `gspread` — Google Sheets integration
- `oauth2client` — Google authentication
- `rd-token` — RD Station OAuth2 (separate package)

See `requirements.txt` for versions.

## Deployment

### Heroku

```bash
heroku create your-app-name
heroku config:set TOTVS_BASE_URL="..." TOTVS_API_KEY="..." ...
heroku config:set RD_CLIENT_ID="..." RD_CLIENT_SECRET="..."
heroku config:set RECIPIENT_EMAIL="..." EMAIL_USER="..." EMAIL_PASS="..."
git push heroku main

# Schedule via Heroku Scheduler
heroku addons:create scheduler:standard
heroku addons:open scheduler
# Add job: python totvs_rd_sync.py (run hourly, daily, or as needed)
```

### Docker

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["python", "totvs_rd_sync.py"]
```

```bash
docker build -t totvs-rd-sync .
docker run -e TOTVS_BASE_URL="..." -e RD_CLIENT_ID="..." totvs-rd-sync
```

## Security

- Never commit `.env` or credential files
- Use strong API keys and passwords
- Restrict Google Sheets service account to needed scopes
- Use Heroku config vars for sensitive data
- Review TOTVS and RD Station API authentication

## License

MIT

## Support

- 📖 [TOTVS API Docs](https://www.totvs.com/developers)
- 🔐 [RD Station API](https://developers.rdstation.com)
- 🐛 [Report Issues](https://github.com/rahima-mokeddem/totvs-rd-sync/issues)

## Contributing

Contributions welcome! Please:
1. Fork the repo
2. Create a feature branch
3. Add tests if possible
4. Submit a PR

## Related Packages

- **[rd-token](https://github.com/rahima-mokeddem/rd-token)** — RD Station OAuth2 token manager

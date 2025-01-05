import base64
import re
import calendar
from datetime import datetime
from bs4 import BeautifulSoup
from googleapiclient.errors import HttpError


def get_deepest_text_payload(payload):
    texts = []

    def extract_text(part):
        if 'parts' in part:
            for subpart in part['parts']:
                extract_text(subpart)
        else:
            mime_type = part.get('mimeType')
            data = part.get('body', {}).get('data')
            if mime_type in ['text/plain', 'text/html'] and data:
                try:
                    decoded = base64.urlsafe_b64decode(data).decode('utf-8', errors='replace')
                    texts.append((mime_type, decoded))
                except Exception:
                    pass

    extract_text(payload)

    plain_texts = [text for mime, text in texts if mime == 'text/plain']
    html_texts = [text for mime, text in texts if mime == 'text/html']

    if plain_texts:
        return ' '.join(plain_texts)
    elif html_texts:
        soup = BeautifulSoup(html_texts[0], 'html.parser')
        return ' '.join(soup.get_text(separator=' ', strip=True).split())
    return ""


def extract_order_id(full_text):
    patterns = [
        r'(Sipariş Numarası[:#]?|Sipariş No[:#]?|Order ID[:#]?|Order Number[:#]?)[^\d]*(\d+)',
        r'(SİPARİŞ NO[:#\.]?|Order ID[:#\.]?)[^\d]*(\d+)',
        r'#(\d+)',
        r'(\d+)\s+numaralı\s+siparişini\s+aldık',
    ]
    return _extract_with_patterns(full_text, patterns)


def extract_amount(full_text):
    text = re.sub(r'\s+', ' ', full_text.replace('\n', ' ').replace('\r', ' '))
    patterns = [
        r'(?:Toplam|Tutar|Amount|Total)[^0-9₺TL$USD€EUR]*?([\d.,]+)\s*(?:TL|TRY|₺|\$|USD|€|EUR)',
        r'([\d.,]+)\s*(?:TL|TRY|₺|\$|USD|€|EUR)',
        r'[₺$€]\s*([\d.,]+)',
    ]
    return _extract_with_patterns(text, patterns, is_amount=True)


def _extract_with_patterns(full_text, patterns, is_amount=False):
    for pattern in patterns:
        match = re.search(pattern, full_text, re.IGNORECASE)
        if match:
            if is_amount:
                amount_str = match.group(1).strip()
                try:
                    if ',' in amount_str:
                        amount_str = amount_str.replace(',', '.')
                    return f"{float(amount_str):.2f}"
                except ValueError:
                    continue
            else:
                return match.group(2) if match.lastindex >= 2 else match.group(1)
    return None


def extract_order_details(html_content):
    return _extract_order_info(html_content, extract_order_id, extract_amount)


def extract_trendyol_order_details(html_content):
    return _extract_order_info(html_content, extract_trendyol_order_id, extract_trendyol_amount)


def _extract_order_info(html_content, order_id_func, amount_func):
    soup = BeautifulSoup(html_content, 'html.parser')
    full_text = ' '.join(soup.get_text(separator=' ', strip=True).split())

    order_id_ = order_id_func(full_text)
    total_amount_ = amount_func(full_text)

    return {
        'order_id': order_id_ or "Sipariş Numarası bulunamadı",
        'total_amount': total_amount_ or "Tutar bulunamadı"
    }


def extract_trendyol_order_id(full_text):
    return _extract_with_patterns(full_text, [
        r"(?:Sipariş Numaranız:|Sipariş Numarası:|Sipariş No:|Order ID:) *(\d+)",
        r"#(\d+)\s+numaralı\s+siparişi",
    ])


def extract_trendyol_amount(full_text):
    return _extract_with_patterns(full_text, [
        r"(?:Sepet Tutarı|Toplam Tutar|Toplam|Ödenecek Tutar)[^\d]*?([\d.,]+)\s*(?:TL|TRY|₺|\$|USD)",
        r'([\d.,]+)\s*(?:TL|TRY|₺|\$|USD)',
        r'[₺$]\s*([\d.,]+)',
    ], is_amount=True)


def list_emails_with_details(service, keywords, max_results=50, query=None):
    return _list_emails(service, keywords, max_results, query, fetch_details=True)


def list_emails_with_month(service, keywords, year, month, max_results=50, query=None):
    start_date, end_date = get_date_range_for_month(year, month)
    query = f"after:{start_date} before:{end_date}"

    return _list_emails(service, keywords, max_results, query)


def _list_emails(service, keywords, max_results, query, fetch_details=False):
    keyword_query = " OR ".join(keywords)
    merged_query = f"{keyword_query} {query}" if query else keyword_query

    try:
        results = service.users().messages().list(userId='me', q=merged_query, maxResults=max_results).execute()
    except HttpError as e:
        print(f"Gmail API error: {e}")
        return []

    messages = results.get('messages', [])
    if not fetch_details:
        return messages

    email_details = []
    for message in messages:
        try:
            msg_data = service.users().messages().get(userId='me', id=message['id']).execute()
            headers = msg_data.get('payload', {}).get('headers', [])
            email_details.append(_extract_email_details(headers))
        except HttpError as e:
            print(f"Gmail API error when fetching message {message['id']}: {e}")
            continue

    return email_details


def _extract_email_details(headers):
    subject = next((h['value'] for h in headers if h['name'] == 'Subject'), 'Unknown')
    sender = next((h['value'] for h in headers if h['name'] == 'From'), 'Unknown')
    date = next((h['value'] for h in headers if h['name'] == 'Date'), 'Unknown')
    return {'subject': subject, 'sender': sender, 'date': date}


def get_date_range_for_month(year, month):
    start_date = f"{year}-{month:02d}-01"
    next_month = month + 1
    next_year = year if next_month < 13 else year + 1
    end_date = f"{next_year}-{(next_month if next_month < 13 else 1):02d}-01"
    return start_date, end_date


def parse_email_date(date_str):
    try:
        return datetime.strptime(date_str, '%a, %d %b %Y %H:%M')
    except ValueError:
        try:
            return datetime.strptime(date_str, '%Y-%m-%d %H:%M:%S')
        except ValueError:
            return None


def is_duplicate_order(existing_orders, new_order):
    new_order_id = new_order.get('order_id')
    if not new_order_id or new_order_id in [order['order_id'] for order in existing_orders]:
        return True
    return False


# Main function for Gmail API interaction (as before)
def main():
    creds = Credentials.from_authorized_user_file('token.json', ['https://www.googleapis.com/auth/gmail.readonly'])
    service = build('gmail', 'v1', credentials=creds)

    orders = process_all_orders(service, max_results=100)

    for order in orders:
        print(f"Sipariş ID: {order['order_id']}, Tutar: {order['amount']}, Kaynak: {order['source']}")
        print(f"Subject: {order['subject']}, Gönderen: {order['sender']}, Tarih: {order['date']}")
        print("-" * 50)


if __name__ == "__main__":
    main()

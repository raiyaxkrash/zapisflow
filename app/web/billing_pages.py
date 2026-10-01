"""Small, dependency-free HTML pages for the external SaaS checkout."""

from html import escape

from app.config.settings import settings
from app.services.billing.checkout_session import CheckoutOffer


def _layout(title: str, content: str) -> str:
    return (
        '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{escape(title)}</title>'
        '<style>body{font:16px/1.5 system-ui,sans-serif;background:#f4f6fb;color:#172033;'
        'margin:0;min-height:100vh;display:grid;place-items:center}.card{background:#fff;'
        'width:min(100% - 32px,440px);padding:32px;border-radius:20px;box-shadow:0 12px 40px #14203a17}'
        'h1{font-size:1.5rem;margin:0 0 18px}.price{font-size:2rem;font-weight:700;margin:8px 0}'
        'label{display:block;margin:20px 0 6px}input{box-sizing:border-box;width:100%;padding:12px;'
        'border:1px solid #aab4c7;border-radius:8px;font:inherit}button{width:100%;margin-top:18px;'
        'padding:13px;border:0;border-radius:8px;background:#275bea;color:#fff;font:inherit;font-weight:700;'
        'cursor:pointer}small{display:block;color:#596579;margin-top:14px}a{color:#275bea}</style>'
        '</head><body><main class="card">'
        f'{content}<small>📞 Поддержка: <a href="{escape(settings.support_url, quote=True)}">'
        f'{escape(settings.support_tag)}</a></small></main></body></html>'
    )


def checkout_page(token: str, offer: CheckoutOffer) -> str:
    amount = f"{offer.amount:,.2f}".replace(",", " ").removesuffix(".00")
    safe_token = escape(token, quote=True)
    return _layout(
        "Подписка ZapisFlow",
        '<h1>💳 Подписка ZapisFlow</h1>'
        f'<p><strong>{escape(offer.plan_name)}</strong></p>'
        f'<p class="price">{amount} ₽</p>'
        f'<p>{offer.period_days} дней</p>'
        f'<form method="post" action="/billing/checkout/{safe_token}/pay">'
        '<label for="email">Email для получения чека</label>'
        '<input id="email" name="email" type="email" autocomplete="email" maxlength="254" required>'
        '<button type="submit">Оплатить</button></form>'
        '<small>После оплаты статус подписки подтвердится после проверки ЮKassa. '
        'Возврат на сайт сам по себе не подтверждает платёж.</small>',
    )


def status_page(title: str, message: str) -> str:
    return _layout(title, f'<h1>{escape(title)}</h1><p>{escape(message)}</p>')

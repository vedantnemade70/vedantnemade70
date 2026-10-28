# PayLink overview

PayLink lets a merchant create a payment link, share it by SMS or email, and get
paid by card. Card details are entered on a PayLink-hosted page.

- Merchants authenticate with an API key sent in the `X-Api-Key` header.
- Card numbers are stored encrypted (AES-256) so customers can pay again with one click.
- Refunds can be issued up to 90 days after payment.

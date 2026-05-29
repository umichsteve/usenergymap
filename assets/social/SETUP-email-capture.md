# Email capture setup (free — Web3Forms)

The `/map` access gate collects name + email and sends each one to your inbox
via **Web3Forms** — free, unlimited submissions, no database, no account to manage.

## One-time setup (≈1 minute)

1. Go to https://web3forms.com
2. Enter the email address where you want leads delivered → an **access key** is emailed to you instantly.
3. Open `map.html`, find the gate config near the bottom, and paste the key:

   ```js
   var WEB3FORMS_KEY = "your-access-key-here";
   ```

4. Commit + push. Done — every signup now lands in your inbox.

That's it. The access key is safe to ship publicly; it only routes submissions to your inbox.

## Notes

- **Export:** Web3Forms emails each lead; the dashboard also offers CSV export and Zapier/webhook forwarding if you later want them in a sheet or list.
- **Dedup:** Web3Forms doesn't dedupe; if you want a clean list later, dedupe on export.
- **No key set?** The gate still appears and the map still works — it just won't capture leads until the key is in.

## Alternative free options (if you'd rather)

- **Google Form → Sheet** — unlimited, data lands in a spreadsheet you own. I'd swap the modal for an embedded form, or POST to the form's response endpoint.
- **Formspree / Tally / Basin** — similar hosted-form services; Formspree's free tier caps at ~50 submissions/month.

## Later: the paid tier

When you graduate to real access control (Next.js + Supabase + Stripe), the
`SETUP-leads.sql` in this folder has the Supabase `leads` table + insert-only
RLS ready to go. The emails captured now seed that launch list.

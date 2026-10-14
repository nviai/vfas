# Requests from the UI team to the core

1. **CSP**: keep `'unsafe-eval'` in `script-src` (the standard Alpine build evaluates `x-data` expressions). **`'unsafe-inline'` can be removed
   from `script-src`**: the new templates no longer contain executable inline `<script>` or `onclick=`; data for JS lives in
   `<script type="application/json">` (not executed). `style-src 'unsafe-inline'` is still needed (`style` attributes in charts, progress bars).
2. **`asset_version`** (optional) in `base_context` to bust the `app.css` cache after each build (templates use `?v=2` by default).
3. **`/companies/new`**: the company switcher (`base.html`) and `auth/select_company.html` link "Thêm công ty / Tạo công ty mới" (Add company / Create a new company)
   to this path – a route is needed (or let us know so we change the path).
4. `user` in the context needs the attributes `full_name`, `email`, `role_label`, `is_superadmin`; `company` needs `name`,
   `short_name`, `tax_code`, `address`. `companies` items may add `tax_code` (the company selection page shows the tax code if present).
5. The `/ui-kit` router (feature `ui_kit`) is only open when `DEBUG_UI=1` or `request.state.user.is_superadmin`; it does not touch the DB.
6. The v1 templates `auth/me.html`, `auth/users.html` have been removed from `app/features/auth/templates` (the core is redoing them) –
   the "Tài khoản của tôi" (My account, `/me`) page needs a new template following pattern (g) Settings.
7. Charts now guard against a zero scale (all values equal / all zero / empty) – fixed per the core's bug report.

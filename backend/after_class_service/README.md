# After-class Follow-up Service

This directory versions the separately deployed follow-up service. It reads student and lesson data through the authenticated main application API and stores follow-up states in its own SQLite database; student records and the live SQLite database do not belong in Git.

The browser module is versioned at `frontend/after-class.js`. Production serves that source as `/after-class.js`; `home-v1.html` cache-busts it with a version query string.

Notification state distinguishes an unchanged sent notice from a notice that was sent and then had its lesson content or homework changed. The latter remains in the resend queue and is labeled accordingly.

# Privacy Policy

**Last updated: 13 June 2026**

This Privacy Policy explains what data the **Percy** Discord bot ("Percy", "the Bot", "we", "us") collects, why we collect it, how long we keep it, who it is shared with, and the rights you have over it. It applies to everyone who uses Percy or is a member of a Discord server ("guild") that Percy is in.

This Policy does not supersede [Discord's Privacy Policy](https://discord.com/privacy) or the [Discord Developer Terms of Service](https://discord.com/developers/docs/policies-and-agreements/developer-terms-of-service). Your use of Discord itself is governed by Discord's own policies.

---

## 1. Who is responsible for your data

The data controller for Percy is **klappstuhlpy** ("the Bot Owner").

- **Contact:** `klappstuhl65@pm.me`
- **Support server:** https://discord.gg/eKwMtGydqh
- **Source code:** https://github.com/klappstuhlpy/Percy-v2

If you have any question about this Policy or wish to exercise your rights (see [Section 7](#7-your-rights)), use the contact details above.

---

## 2. What we collect, why, and on what legal basis

We only collect what a feature needs to work. Under the EU/UK GDPR, our legal bases are **consent** (for optional tracking you can turn on/off) and **legitimate interest / performance of a service** (for data that is strictly required to run a command you invoked).

| Data                                                                                             | Purpose                                                                    | Legal basis                           | Default              |
|--------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------|---------------------------------------|----------------------|
| **Discord IDs** (user, server, channel, role, message)                                           | Identify where settings, content and actions belong                        | Service necessity                     | Always (required)    |
| **Presence history** (online/idle/dnd/offline transitions)                                       | Presence graphs and statistics                                             | Consent                               | **On — opt-out**     |
| **Username & nickname history**                                                                  | "Name history" feature                                                     | Consent                               | **On — opt-out**     |
| **Avatar history** (image snapshots)                                                             | "Avatar history" feature                                                   | Consent                               | **On — opt-out**     |
| **Server configuration & moderation logs**                                                       | Run the features a server admin enables (logging, automod, leveling, etc.) | Service necessity / admin instruction | Set by server admins |
| **Message & voice activity counts**                                                              | Leveling / XP                                                              | Service necessity (where enabled)     | Set by server admins |
| **Content you submit to a command** (reminders, tags, playlists, giveaways, notes, poll answers) | Provide that feature                                                       | Service necessity                     | When you use it      |
| **Linked accounts** (e.g. AniList) and **timezone**                                              | Features you opt into                                                      | Consent                               | When you set it      |

We **do not** sell your data, and we **do not** use it for advertising or profiling beyond the features described above.

### Tracking is on by default, but you control it

Presence, name and avatar history tracking are **enabled by default** so the related features work out of the box. You can change this at any time, for your own account, with these commands:

- `settings tracking false` — turn **all** tracking off in one go
- `settings presence false` — turn off only presence tracking
- `settings history false` — turn off only name/nickname/avatar history
- `settings show` — see your current settings

A server administrator cannot consent to this tracking on your behalf; the choice is always yours and applies to your account across every server.

---

## 3. How long we keep it

- **Presence history:** automatically deleted after **30 days**.
- **Username, nickname and avatar history:** kept until you delete it (see [Section 7](#7-your-rights)) or until Percy is removed and the data is purged.
- **Server configuration & logs:** kept while Percy is in the server; configuration for a server is deleted when Percy is removed from it.
- **Feature content** (reminders, tags, etc.): kept until the item is completed, deleted by you, or no longer needed for the feature.

When data is no longer needed for its purpose, it is deleted.

---

## 4. Who we share it with

Percy is hosted on secured private servers. To provide certain features, some data is sent to the following third-party processors **only when you use the relevant feature**:

| Service                                              | What is sent                                                                              | When                                   |
|------------------------------------------------------|-------------------------------------------------------------------------------------------|----------------------------------------|
| **Discord**                                          | All bot activity (Discord is the platform)                                                | Always                                 |
| **Groq** (`api.groq.com`)                            | The text of a message you send to the AI assistant                                        | When you use the AI assistant          |
| **Google Translate** (`translate.googleapis.com`)    | The text you ask to translate (including a message you select via the "Translate" action) | When you use translation               |
| **AniList** (`anilist.co`)                           | OAuth authorization to link your AniList account                                          | When you link AniList                  |
| **Lavalink** (self-hosted, `lavalink.klappstuhl.me`) | Music search queries and track URLs                                                       | When you use music                     |
| **klappstuhl.me dashboard**                          | Discord OAuth login and the configuration you change in the web dashboard                 | When a server admin uses the dashboard |

Each third party processes data under its own privacy policy. We do not send these services more than the feature requires.

---

## 5. Children

Percy is not directed at children. In line with Discord's Terms of Service, you must be at least **13 years old** (or the minimum digital-consent age in your country, if higher) to use Percy. We do not knowingly collect data from anyone below that age; if we learn that we have, we will delete it.

---

## 6. Security & international transfers

Stored data is held on access-controlled servers, and access is limited to what is needed to operate the Bot. No method of transmission or storage is completely secure, so we cannot guarantee absolute security.

Some processors listed in [Section 4](#4-who-we-share-it-with) (for example Groq, a US-based provider) may process data outside your country, including outside the EEA. Where that happens, the transfer is limited to the data the feature requires.

---

## 7. Your rights

Regardless of where you live, you can exercise the following rights over your personal data. Some are available directly through bot commands; for anything else, contact us (see [Section 1](#1-who-is-responsible-for-your-data)).

- **Access / portability** — get a copy of your stored data: `settings request-data` (Percy sends you a JSON export).
- **Erasure** — permanently delete your stored presence, name/nickname and avatar history: `settings remove-personal-data`.
- **Object / restrict** — stop future tracking: `settings tracking false` (or the per-type toggles).
- **Rectification** — ask us to correct inaccurate data via the contact details above.

Under the GDPR you also have the right to lodge a complaint with your local data protection authority. These rights cannot be waived, and nothing in this Policy asks you to give them up.

---

## 8. Changes to this Policy

We may update this Policy from time to time. Material changes will be noted by updating the "Last updated" date at the top and, where appropriate, announced in the support server. Continued use of Percy after a change means you accept the updated Policy.

---

## 9. Agreement

By adding Percy to a server, or by using Percy as a member of such a server, you acknowledge this Privacy Policy. Server administrators are responsible for making their members aware of this Policy and of [Discord's Terms](https://discord.com/terms).

- If you are a **server administrator** and do not agree, you may remove Percy from your server.
- If you are a **server member** and do not agree, you may leave the server or disable tracking and delete your data using the commands above.

// Data helpers shared by the views: loading, search, filters, sectors, CSV.

export const STATUS_TAG = { "New": "neutral", "To contact": "warning", "Contacted": "info", "Replied": "success", "Meeting": "success", "Not a fit": "danger" };

// The web lookup writes free-text industries; fold them into a few sectors you can filter by.
export const SECTORS = [["Trading & brokers", /forex|\bfx\b|broker|trading|prop firm|cfd/i],
  ["Fintech & payments", /payment|fintech|bank|crypto|wallet|exchange|psp|card/i],
  ["iGaming", /gambl|casino|betting|igaming|sportsbook|slot|lotto/i],
  ["Affiliate & lead gen", /affiliate|cpa|lead gen|lead generation|performance/i],
  ["Ads & media", /advert|ad network|adtech|programmatic|media|marketing|traffic|seo|agency/i]];
export const SECTOR_ORDER = [...SECTORS.map(s => s[0]), "Other"];

function sector(industry, about) {
  for (const [name, re] of SECTORS) if (re.test(industry || "")) return name;
  for (const [name, re] of SECTORS) if (re.test(about || "")) return name;
  return industry || about ? "Other" : "";
}

export async function load() {
  const d = await (await fetch("/api/data")).json();
  for (const p of d.people) {
    const co = d.companies[p.company_key] || {};
    p.coName = co.name || p.company || "";
    p.sector = sector(co.industry, co.about);
    p.display = p.name || p.company || p.emails[0] || p.telegram[0] || "Unnamed card";
    p.bare = !p.name && !p.emails.length && !p.phones.length && !p.telegram.length;  // logo backs and the like
    p.updated = p.notes[0]?.at || "";
    p.hay = [p.display, p.title, p.coName, p.company, ...p.emails, ...p.phones, ...p.telegram, p.website, p.city, p.country, p.other,
      p.sector, ...p.tags, ...p.notes.map(n => n.text), ...Object.values(p.tg).map(t => t.name), ...p.qr.map(q => q.url || q.text)]
      .join(" ").toLowerCase();
  }
  return d;
}

export const reach = {
  telegram: p => p.telegram.length, email: p => p.emails.length, phone: p => p.phones.length, qr: p => p.qr.length,
};

export function filterPeople(people, f) {
  const words = f.q.toLowerCase().split(/\s+/).filter(Boolean);
  return people.filter(p => !p.bare
    && (!f.status || p.status === f.status) && (!f.tag || p.tags.includes(f.tag)) && (!f.sector || p.sector === f.sector)
    && (!f.starred || p.starred) && (!f.reach || reach[f.reach](p))
    && words.every(w => p.hay.includes(w)));
}

export function sortPeople(list, by) {
  const cmp = {
    company: (a, b) => (!a.coName - !b.coName) || a.coName.localeCompare(b.coName) || a.display.localeCompare(b.display),
    name: (a, b) => a.display.localeCompare(b.display),
    recent: (a, b) => (b.updated || "").localeCompare(a.updated || "") || b.id - a.id,
  }[by];
  return [...list].sort(cmp);
}

export function companiesOf(people, companies, q, sectorFilter) {
  const words = q.toLowerCase().split(/\s+/).filter(Boolean);
  const map = {};
  for (const p of people) if (p.company_key) (map[p.company_key] ||= []).push(p);
  return Object.entries(map).map(([key, ps]) => ({ key, ps, co: companies[key] || { name: ps[0].company || key }, sector: ps[0].sector }))
    .filter(c => (!sectorFilter || c.sector === sectorFilter)
      && words.every(w => [c.co.name, c.co.about, c.co.industry, c.co.hq, c.key, ...c.ps.map(p => p.hay)].join(" ").toLowerCase().includes(w)))
    .sort((a, b) => (a.co.name || "").localeCompare(b.co.name || ""));
}

export function exportCsv(list) {
  const cols = ["Name", "Title", "Company", "Sector", "Email", "Phone", "Telegram", "Website", "LinkedIn", "QR link", "Status", "Starred", "Tags", "Latest note"];
  const rows = list.map(p => [p.display, p.title, p.coName, p.sector, p.emails.join("; "), p.phones.join("; "), p.telegram.join("; "),
    p.website, p.linkedin, p.qr.filter(q => q.url).map(q => q.url).join("; "), p.status, p.starred ? "yes" : "", p.tags.join("; "), p.notes[0]?.text || ""]);
  const csv = "﻿" + [cols, ...rows].map(r => r.map(v => `"${String(v ?? "").replace(/"/g, '""')}"`).join(",")).join("\r\n");
  const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(new Blob([csv], { type: "text/csv" })), download: "contacts.csv" });
  a.click();
  return rows.length;
}

export async function api(url, method, body) {
  const r = await fetch(url, { method, headers: { "Content-Type": "application/json" }, body: body && JSON.stringify(body) });
  const j = await r.json();
  if (!r.ok) throw new Error(j.error || "Could not save. Is crm.py still running?");
  return j;
}

export const shortUrl = u => u.replace(/^https?:\/\/(www\.)?/, "").replace(/\/$/, "");
export const href = u => (/^https?:/.test(u) ? u : "https://" + u);

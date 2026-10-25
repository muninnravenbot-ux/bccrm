import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  Button, Callout, Card, CardHeader, Chips, IconButton, ImageGallery, Input, Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
  Tabs, TabsList, TabsTrigger, Tag, TagBlock, TextArea, ThemeProvider,
} from "@openuidev/react-ui";
import {
  Building2, Download, Globe, Linkedin, Mail, MessageCircle, Moon, Phone, QrCode, Search, Send, Star, Sun, Trash2, Users, X,
} from "lucide-react";
import { SECTOR_ORDER, STATUS_TAG, api, companiesOf, exportCsv, filterPeople, href, load, reach, shortUrl, sortPeople } from "./data.js";

const thumb = path => `/thumbs/${path.split("/").pop()}`;  // small cached copy; the gallery uses the full crop
const systemMode = () => (window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light");
const EDIT_FIELDS = [["full_name", "Name", "name"], ["job_title", "Title", "title"], ["company", "Company", "company"], ["emails", "Email", "emails"],
  ["phones", "Phone", "phones"], ["telegram", "Telegram", "telegram"], ["website", "Website", "website"], ["linkedin", "LinkedIn", "linkedin"],
  ["address", "Address", "address"], ["city", "City", "city"], ["country", "Country", "country"]];

export default function App() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [mode, setMode] = useState(() => localStorage.getItem("bccrm-mode") || systemMode());
  const [view, setView] = useState("people");
  const [f, setF] = useState({ q: "", status: null, tag: null, sector: null, starred: false, reach: null });
  const [sort, setSort] = useState("company");
  const [sel, setSel] = useState(null);
  const [toast, setToast] = useState(null);
  const [showDetail, setShowDetail] = useState(false);  // phones: list or person
  const [showFilters, setShowFilters] = useState(false);  // phones: filters fold away so the list comes first
  const [, bump] = useState(0);
  const search = useRef(null);

  useEffect(() => {
    load().then(d => {
      setData(d);
      const h = location.hash.match(/^#(p|c)(.+)$/);
      if (h) { setView(h[1] === "p" ? "people" : "companies"); setSel(h[1] === "p" ? +h[2] : decodeURIComponent(h[2])); setShowDetail(true); }
    }).catch(() => setError("Can't reach the contacts server. Start it with: python crm.py"));
  }, []);
  useEffect(() => {
    document.documentElement.setAttribute("data-openui-theme", mode);  // OpenUI's defaults follow the OS unless this is set
    try { localStorage.setItem("bccrm-mode", mode); } catch {}
  }, [mode]);

  const people = data?.people || [];
  const list = useMemo(() => data && sortPeople(filterPeople(people, f), sort), [data, f, sort, people.map(p => p.status + p.starred + p.tags).join()]);
  const companies = useMemo(() => data && companiesOf(people, data.companies, f.q, f.sector), [data, f.q, f.sector]);
  const ids = view === "people" ? (list || []).map(p => p.id) : (companies || []).map(c => c.key);

  const flash = msg => { setToast(msg); clearTimeout(flash.t); flash.t = setTimeout(() => setToast(null), 1800); };
  const select = id => {
    setSel(id); setShowDetail(true);
    history.replaceState(null, "", view === "people" ? `#p${id}` : `#c${encodeURIComponent(id)}`);
  };
  const goPerson = id => { setView("people"); setF({ q: "", status: null, tag: null, sector: null, starred: false, reach: null }); setSel(id);
    history.replaceState(null, "", `#p${id}`); setShowDetail(true); };
  const goCompany = key => { setView("companies"); setF(x => ({ ...x, q: "", sector: null })); setSel(key);
    history.replaceState(null, "", `#c${encodeURIComponent(key)}`); setShowDetail(true); };
  const save = async (p, patch, msg) => {
    try { await api(`/api/contact/${p.id}`, "POST", patch); Object.assign(p, patch.fields ? {} : patch); bump(x => x + 1); flash(msg); }
    catch (e) { flash(e.message); }
  };

  useEffect(() => {
    const onKey = e => {
      if (/^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName) || document.querySelector("[role=dialog]")) return;
      if (e.key === "/") { e.preventDefault(); search.current?.focus(); }
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        if (!ids.length) return;
        const i = ids.indexOf(sel);
        select(ids[Math.max(0, Math.min(ids.length - 1, i < 0 ? 0 : i + (e.key === "ArrowDown" ? 1 : -1)))]);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });
  useEffect(() => { document.querySelector(".row.is-selected")?.scrollIntoView({ block: "nearest" }); }, [sel]);

  const visible = people.filter(p => !p.bare);
  const count = fn => visible.filter(fn).length;
  const chipsOf = (items, cur, key) => (
    <Chips type="single" selected={cur ? [cur] : []} onToggle={v => setF(x => ({ ...x, [key]: x[key] === v ? null : v }))}
      items={items.map(([value, label]) => ({ value, label }))} />
  );
  const allTags = [...new Set(visible.flatMap(p => p.tags))].sort();
  const person = view === "people" && people.find(p => p.id === sel);

  return (
    <ThemeProvider mode={mode} cssSelector=":root">{/* :root, so portalled dialogs (the photo gallery) get the theme too */}
      <div className={`shell${showDetail ? " show-detail" : ""}`}>
        <aside className="side">
          <div className="brand-row">
            <div className="brand">BCCRM</div>
            <IconButton variant="tertiary" size="small" icon={mode === "dark" ? <Sun size={16} /> : <Moon size={16} />}
              aria-label="Switch light or dark" onClick={() => setMode(m => (m === "dark" ? "light" : "dark"))} />
          </div>
          <div className="search">
            <Search size={16} className="search-icon" />
            <Input ref={search} value={f.q} placeholder="Search names, companies, notes" aria-label="Search"
              onChange={e => setF(x => ({ ...x, q: e.target.value }))} />
          </div>
          <Tabs value={view} onValueChange={v => { setView(v); setSel(null); }}>
            <TabsList>
              <TabsTrigger value="people" text="People" icon={<Users size={15} />} />
              <TabsTrigger value="companies" text="Companies" icon={<Building2 size={15} />} />
            </TabsList>
          </Tabs>
          {data && (() => {
            const active = [f.status, f.reach, f.tag, f.sector, f.starred].filter(Boolean).length;
            return <Button className="filters-toggle" variant="secondary" size="small" aria-expanded={showFilters}
              onClick={() => setShowFilters(s => !s)}>{showFilters ? "Hide filters" : active ? `Filters (${active} on)` : "Filters"}</Button>;
          })()}
          {data && (
            <div className={`facets${showFilters ? " open" : ""}`}>
              {view === "people" && <>
                <section><h3>Status</h3>{chipsOf(data.statuses.map(s => [s, `${s} ${count(p => p.status === s)}`]), f.status, "status")}</section>
                <section><h3>Reachable by</h3>{chipsOf([["telegram", `Telegram ${count(reach.telegram)}`], ["email", `Email ${count(reach.email)}`],
                  ["phone", `Phone ${count(reach.phone)}`], ["qr", `QR code ${count(reach.qr)}`]], f.reach, "reach")}</section>
                <section><h3>Saved</h3>
                  <Chips type="multiple" selected={f.starred ? ["star"] : []} onToggle={() => setF(x => ({ ...x, starred: !x.starred }))}
                    items={[{ value: "star", label: `Starred ${count(p => p.starred)}`, icon: <Star size={13} /> }]} />
                  {allTags.length > 0 && chipsOf(allTags.map(t => [t, `${t} ${count(p => p.tags.includes(t))}`]), f.tag, "tag")}
                </section>
              </>}
              <section><h3>Sector</h3>{chipsOf(SECTOR_ORDER.filter(s => count(p => p.sector === s)).map(s => [s, `${s} ${count(p => p.sector === s)}`]), f.sector, "sector")}</section>
              {view === "people" && <Button variant="secondary" size="small" iconLeft={<Download size={15} />}
                onClick={() => flash(`Exported ${exportCsv(list)} people`)}>Export this list (CSV)</Button>}
            </div>
          )}
        </aside>

        <main className="list">
          <div className="list-head">
            <span className="count">{view === "people" ? `${list?.length ?? 0} people` : `${companies?.length ?? 0} companies`}</span>
            {view === "people" && (
              <div className="sort">
                <Select value={sort} onValueChange={setSort} size="sm">
                  <SelectTrigger aria-label="Sort"><SelectValue /></SelectTrigger>
                  <SelectContent position="popper">
                    <SelectItem value="company">By company</SelectItem>
                    <SelectItem value="name">By name</SelectItem>
                    <SelectItem value="recent">Recently updated</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            )}
          </div>
          <div className="rows" role="listbox" aria-label="Results">
            {error && <Callout variant="danger" title="Not connected" description={error} />}
            {view === "people" && list?.map(p => (
              <button key={p.id} role="option" aria-selected={sel === p.id} className={`row${sel === p.id ? " is-selected" : ""}`} onClick={() => select(p.id)}>
                <img className="thumb" src={thumb(p.photos[0])} alt="" loading="lazy" />
                <span className="row-text"><span className="name">{p.display}</span><span className="sub">{[p.title, p.coName].filter(Boolean).join(", ")}</span></span>
                <span className="meta">
                  {p.starred && <Star size={13} className="star" />}
                  {p.qr.length > 0 && <QrCode size={13} className="faint" />}
                  {p.status !== "New" && <Tag size="sm" variant={STATUS_TAG[p.status]} text={p.status} />}
                </span>
              </button>
            ))}
            {view === "people" && list?.length === 0 && <p className="empty">Nobody matches. Clear the search or pick another filter.</p>}
            {view === "companies" && companies?.map(c => (
              <button key={c.key} role="option" aria-selected={sel === c.key} className={`row${sel === c.key ? " is-selected" : ""}`} onClick={() => select(c.key)}>
                <img className="thumb" src={thumb(c.ps[0].photos[0])} alt="" loading="lazy" />
                <span className="row-text"><span className="name">{c.co.name || c.key}</span><span className="sub">{c.co.industry || c.key}</span></span>
                <span className="meta faint">{c.ps.filter(p => !p.bare).length || ""}</span>
              </button>
            ))}
          </div>
        </main>

        <section className="detail" aria-live="polite">
          {person ? <PersonView key={person.id} p={person} data={data} save={save} flash={flash} bump={() => bump(x => x + 1)}
              goPerson={goPerson} goCompany={goCompany} back={() => setShowDetail(false)} allTags={allTags} />
            : view === "companies" && sel ? <CompanyView key={sel} k={sel} data={data} goPerson={goPerson} back={() => setShowDetail(false)} />
            : <div className="placeholder"><h2>{view === "people" ? "Pick someone from the list" : "Pick a company"}</h2>
                <p>{view === "people" ? "Press / to search, ↑ ↓ to move through people." : "Everyone you met there, with what the company does."}</p></div>}
        </section>
      </div>
      {toast && <div className="toast" role="status">{toast}</div>}
    </ThemeProvider>
  );
}

function ReachButtons({ p }) {
  const tgHandles = p.telegram.map(t => t.replace(/^@/, "").toLowerCase());
  const dup = u => { const m = u.match(/t\.me\/([\w_]+)/i); return (m && tgHandles.includes(m[1].toLowerCase())) || (p.linkedin && shortUrl(u) === shortUrl(p.linkedin)); };
  const open = (url, newTab = true) => () => (newTab ? window.open(url, "_blank", "noopener") : (location.href = url));
  const b = [];
  p.emails.forEach(e => b.push(<Button key={e} variant="primary" size="small" iconLeft={<Mail size={15} />} onClick={open(`mailto:${e}`, false)}>{e}</Button>));
  p.telegram.forEach(t => b.push(<Button key={t} variant="secondary" size="small" iconLeft={<Send size={15} />}
    title={p.tg[t] ? `Telegram ${p.tg[t].kind}: ${p.tg[t].name}` : "Not confirmed on t.me"} onClick={open(`https://t.me/${t.replace(/^@/, "")}`)}>{t}</Button>));
  p.phones.forEach(ph => {
    b.push(<Button key={ph} variant="secondary" size="small" iconLeft={<Phone size={15} />} onClick={open(`tel:${ph}`, false)}>{ph}</Button>);
    if (ph.startsWith("+")) b.push(<IconButton key={ph + "wa"} variant="tertiary" size="small" icon={<MessageCircle size={15} />} aria-label="WhatsApp"
      title="WhatsApp" onClick={open(`https://wa.me/${ph.replace(/\D/g, "")}`)} />);
  });
  p.qr.forEach((q, i) => {
    if (q.kind === "link" && !dup(q.url)) b.push(<Button key={"qr" + i} variant="secondary" size="small" iconLeft={<QrCode size={15} />}
      title={`QR code on the card${q.final ? `, opens ${q.final}` : ""}`} onClick={open(q.url)}>{shortUrl(q.url)}</Button>);
    if (q.kind === "vcard") b.push(<Button key={"vc" + i} variant="secondary" size="small" iconLeft={<QrCode size={15} />}
      onClick={() => Object.assign(document.createElement("a"), { href: `data:text/vcard;charset=utf-8,${encodeURIComponent(q.text)}`,
        download: `${(p.name || "contact").replace(/[^\w\- ]+/g, "")}.vcf` }).click()}>Save contact (.vcf)</Button>);
  });
  if (p.linkedin) b.push(<Button key="li" variant="secondary" size="small" iconLeft={<Linkedin size={15} />} onClick={open(href(p.linkedin))}>LinkedIn</Button>);
  (p.website || "").split(/[\s,;|]+/).filter(w => w.includes(".")).forEach(w => b.push(
    <Button key={w} variant="secondary" size="small" iconLeft={<Globe size={15} />} onClick={open(href(w))}>{shortUrl(w)}</Button>));
  return b.length ? <div className="reach">{b}</div> : <p className="faint">No contact details on this card.</p>;
}

function PersonView({ p, data, save, flash, bump, goPerson, goCompany, back, allTags }) {
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState({});
  const [note, setNote] = useState("");
  const [tagIn, setTagIn] = useState("");
  const co = data.companies[p.company_key];

  const addNote = async () => {
    if (!note.trim()) return flash("Write something first");
    try { const n = await api(`/api/contact/${p.id}/notes`, "POST", { text: note }); p.notes.unshift(n); p.updated = n.at; setNote(""); bump(); flash("Note added"); }
    catch (e) { flash(e.message); }
  };
  const delNote = async id => {
    if (!confirm("Delete this note?")) return;
    await api(`/api/notes/${id}`, "DELETE"); p.notes = p.notes.filter(n => n.id !== id); bump(); flash("Note deleted");
  };
  const addTag = () => {
    const v = tagIn.trim();
    if (v && !p.tags.includes(v)) save(p, { tags: [...p.tags, v].sort() }, `Tagged ${v}`);
    setTagIn("");
  };
  const startEdit = () => {
    setForm(Object.fromEntries(EDIT_FIELDS.map(([k, , pk]) => [k, Array.isArray(p[pk]) ? p[pk].join(", ") : p[pk] || ""]))); setEditing(true);
  };
  const saveEdit = async () => {
    const list = v => v.split(/[,;]\s*/).map(x => x.trim()).filter(Boolean);
    const fields = Object.fromEntries(EDIT_FIELDS.map(([k]) => [k, ["emails", "phones", "telegram"].includes(k) ? list(form[k]) : form[k].trim() || null]));
    fields.telegram = fields.telegram.map(x => (x.startsWith("@") ? x : "@" + x));
    try {
      await api(`/api/contact/${p.id}`, "POST", { fields });
      EDIT_FIELDS.forEach(([k, , pk]) => { p[pk] = fields[k]; });
      p.display = p.name || p.company || p.emails[0] || "Unnamed card"; p.review = false;
      setEditing(false); bump(); flash("Changes saved");
    } catch (e) { flash(e.message); }
  };

  const mates = data.people.filter(q => q.company_key === p.company_key && q.id !== p.id && !q.bare);
  const tgNotes = Object.entries(p.tg).map(([h, t]) => `${h} is a Telegram ${t.kind} named “${t.name}”`).join(". ");
  const rows = EDIT_FIELDS.map(([, l, pk]) => [l, Array.isArray(p[pk]) ? p[pk].join(", ") : p[pk]]).filter(([, v]) => v);

  return (
    <div className="detail-inner">
      <Button className="back" variant="tertiary" size="small" onClick={back}>Back to list</Button>
      <div className="hero">
        <div className="gallery">
          <ImageGallery images={p.photos.map((src, i) => ({ src: `/${src}`, alt: i ? `Photo ${i + 1} of ${p.display}'s card` : `Business card of ${p.display}`,
            details: `Photo ${i + 1} of ${p.photos.length}` }))} />
        </div>
        <div className="who">
          <h1>{p.display}</h1>
          {(() => {  // "Title at Company", unless the title already names the company ("CEO & Founder at Yippie")
            const named = p.title && p.coName && p.title.toLowerCase().includes(p.coName.toLowerCase());
            const co = p.company_key && p.coName
              ? <a href={`#c${encodeURIComponent(p.company_key)}`} onClick={e => { e.preventDefault(); goCompany(p.company_key); }}>{p.coName}</a> : p.coName;
            return <div className="role">{named ? p.title : <>{p.title}{p.title && p.coName ? " at " : ""}{co}</>}</div>;
          })()}
          <ReachButtons p={p} />
          <Chips type="single" selected={[p.status]} onToggle={s => s !== p.status && save(p, { status: s }, `Status set to ${s}`)}
            items={data.statuses.map(s => ({ value: s, label: s }))} />
          <div className="tags">
            <IconButton variant={p.starred ? "primary" : "secondary"} size="small" icon={<Star size={15} />} aria-pressed={p.starred}
              aria-label={p.starred ? "Remove star" : "Star"} onClick={() => save(p, { starred: !p.starred }, p.starred ? "Star removed" : "Starred")} />
            {p.tags.length > 0 && <TagBlock>{p.tags.map(t => (
              <span key={t} className="tag-wrap"><Tag size="md" text={t} /><button className="untag" aria-label={`Remove tag ${t}`}
                onClick={() => save(p, { tags: p.tags.filter(x => x !== t) }, "Tag removed")}><X size={12} /></button></span>))}</TagBlock>}
            <Input size="small" className="tag-input" placeholder="Add a tag" value={tagIn} aria-label="Add a tag, then press Enter"
              onChange={e => setTagIn(e.target.value)} onKeyDown={e => e.key === "Enter" && addTag()} />
          </div>
          {(() => {  // existing tags you can reuse with one click (instead of a native datalist popup)
            const q = tagIn.trim().toLowerCase();
            const sugg = allTags.filter(t => !p.tags.includes(t) && (!q || t.toLowerCase().includes(q))).slice(0, 6);
            return sugg.length > 0 && <div className="tag-suggest"><span className="faint">Use:</span>{sugg.map(t => (
              <button key={t} className="tag-pick" onClick={() => save(p, { tags: [...p.tags, t].sort() }, `Tagged ${t}`)}><Tag size="sm" text={t} /></button>))}</div>;
          })()}
        </div>
      </div>

      <Card variant="sunk" width="full" className="block">
        <CardHeader title="Notes" />
        <TextArea rows={3} placeholder="What did you talk about? What's next?" value={note} onChange={e => setNote(e.target.value)} aria-label="New note" />
        <div className="row-actions"><Button variant="primary" size="small" onClick={addNote}>Add note</Button></div>
        {p.notes.map(n => (
          <div key={n.id} className="note">
            <div className="when"><span>{n.at}</span>
              <IconButton variant="tertiary" size="2-extra-small" icon={<Trash2 size={13} />} aria-label="Delete note" onClick={() => delNote(n.id)} /></div>
            {n.text}
          </div>
        ))}
      </Card>

      <Card variant="sunk" width="full" className="block">
        <CardHeader title="Details" actions={editing
          ? [<Button key="c" variant="tertiary" size="small" onClick={() => setEditing(false)}>Cancel</Button>,
             <Button key="s" variant="primary" size="small" onClick={saveEdit}>Save changes</Button>]
          : <Button variant="secondary" size="small" onClick={startEdit}>Edit</Button>} />
        <dl className="fields">
          {editing ? EDIT_FIELDS.map(([k, l]) => (
            <React.Fragment key={k}><dt><label htmlFor={`f-${k}`}>{l}</label></dt>
              <dd><Input id={`f-${k}`} size="small" value={form[k]} onChange={e => setForm(x => ({ ...x, [k]: e.target.value }))} /></dd></React.Fragment>))
          : <>
            {rows.map(([l, v]) => <React.Fragment key={l}><dt>{l}</dt><dd>{v}</dd></React.Fragment>)}
            {p.other && <><dt>On the card</dt><dd>{p.other}</dd></>}
            {tgNotes && <><dt>Checked</dt><dd className="faint">{tgNotes}</dd></>}
            {p.qr.length > 0 && <><dt>QR code</dt><dd>{p.qr.map((q, i) => (
              <div key={i}>{q.kind === "link" ? <><a href={q.url} target="_blank" rel="noopener">{q.url}</a>{q.final && <span className="faint"> opens {shortUrl(q.final)}</span>}</>
                : q.kind === "vcard" ? `Contact card (vCard): ${(q.text.match(/^FN[^:]*:(.*)$/m) || [, "contact"])[1]}` : q.text}</div>))}</dd></>}
            <dt>Photographed</dt><dd className="faint">{p.source}</dd>
          </>}
        </dl>
      </Card>

      {(co || mates.length > 0) && (
        <Card variant="card" width="full" className="block">
          <CardHeader title={co?.name || p.coName} subtitle={[co?.industry, co?.hq].filter(Boolean).join(", ")}
            actions={<Button variant="secondary" size="small" onClick={() => goCompany(p.company_key)}>Open company</Button>} />
          <CompanyBody co={co} mates={mates} goPerson={goPerson} />
        </Card>
      )}
    </div>
  );
}

function CompanyBody({ co, mates, goPerson }) {
  return (
    <div className="company-body">
      {co?.about && <p>{co.about}</p>}
      {co?.what && co.what !== co.about && <p className="faint">{co.what}</p>}
      <div className="reach">
        {co?.website && <Button variant="secondary" size="small" iconLeft={<Globe size={15} />} onClick={() => window.open(href(co.website), "_blank", "noopener")}>{co.website}</Button>}
        {Object.entries(co?.socials || {}).map(([k, u]) => (
          <Button key={k} variant="tertiary" size="small" onClick={() => window.open(u, "_blank", "noopener")}>{k[0].toUpperCase() + k.slice(1)}</Button>))}
      </div>
      {mates.length > 0 && <>
        <h4>Also met here</h4>
        <Chips type="single" selected={[]} onToggle={id => goPerson(+id)}
          items={mates.map(m => ({ value: String(m.id), label: m.title ? `${m.display}, ${m.title}` : m.display }))} />
      </>}
    </div>
  );
}

function CompanyView({ k, data, goPerson, back }) {
  const ps = data.people.filter(p => p.company_key === k);
  const co = data.companies[k] || { name: ps[0]?.company || k };
  const people = ps.filter(p => !p.bare), backs = ps.filter(p => p.bare).flatMap(p => p.photos);
  return (
    <div className="detail-inner">
      <Button className="back" variant="tertiary" size="small" onClick={back}>Back to list</Button>
      <div className="who"><h1>{co.name || k}</h1><div className="role">{[co.industry, co.hq].filter(Boolean).join(", ")}</div></div>
      <Card variant="card" width="full" className="block">
        <CompanyBody co={co} mates={[]} goPerson={goPerson} />
        {!co.about && <p className="faint">No web profile for this company yet.</p>}
      </Card>
      <Card variant="sunk" width="full" className="block">
        <CardHeader title={`People (${people.length})`} />
        {people.length ? <div className="people-grid">{people.map(m => (
          <button key={m.id} className="person-tile" onClick={() => goPerson(m.id)}>
            <img src={thumb(m.photos[0])} alt="" loading="lazy" /><span className="name">{m.display}</span><span className="sub">{m.title}</span>
          </button>))}</div> : <p className="faint">Only a card back from this company.</p>}
      </Card>
      {backs.length > 0 && <Card variant="sunk" width="full" className="block"><CardHeader title="Card backs" />
        <ImageGallery images={backs.map(src => ({ src: `/${src}`, alt: "Card back" }))} /></Card>}
    </div>
  );
}

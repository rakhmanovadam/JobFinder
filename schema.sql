create table if not exists control (
  key text primary key, value jsonb, updated_at timestamptz default now()
);
insert into control (key, value) values
  ('paused', 'false'::jsonb),
  ('applies_today', '0'::jsonb),
  ('applies_this_hour', '0'::jsonb)
on conflict (key) do nothing;

create table if not exists jobs (
  id uuid primary key default gen_random_uuid(),
  source text not null default 'linkedin',
  external_id text not null,
  company text, title text, location text, remote boolean,
  jd_text text,
  ats_type text,
  ats_url text,
  apply_lane text,
  posted_at timestamptz,
  persona text,
  matched boolean default false,
  match_score numeric,
  seen_at timestamptz default now(),
  unique(source, external_id)
);

create table if not exists applications (
  id uuid primary key default gen_random_uuid(),
  job_id uuid references jobs(id),
  status text default 'drafted',
  resume_pdf text, resume_docx text,
  tailored_json jsonb,
  cover_note text, tailored_from text,
  validation_passed boolean,
  tg_message_id bigint, tg_chat_id bigint,
  attempts int default 0,
  created_at timestamptz default now(), applied_at timestamptz, error text
);

create table if not exists resume_master (
  id uuid primary key default gen_random_uuid(),
  section text,
  persona text[],
  content jsonb
);

create table if not exists field_answers (
  id uuid primary key default gen_random_uuid(),
  field_label text, field_type text, answer jsonb, source text,
  unique(field_label, field_type)
);

create table if not exists run_log (
  id uuid primary key default gen_random_uuid(),
  cycle_at timestamptz default now(),
  found int, matched int, drafted int, applied int, failed int, notes text
);

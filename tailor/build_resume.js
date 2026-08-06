// Renders tailored JSON into a Harvard-style one-page .docx
// Usage: node build_resume.js <tailored.json> <out.docx>
const fs = require("fs");
const {
  Document, Packer, Paragraph, TextRun, TabStopType, AlignmentType, BorderStyle,
} = require("docx");

const [, , inPath, outPath] = process.argv;
const data = JSON.parse(fs.readFileSync(inPath, "utf8"));
const c = data.contact;

const RIGHT_TAB = 10800; // ~7.5in usable width at 0.5in margins

const line = (children, opts = {}) =>
  new Paragraph({
    children,
    tabStops: [{ type: TabStopType.RIGHT, position: RIGHT_TAB }],
    spacing: { after: opts.after ?? 0 },
    alignment: opts.align,
  });

const sectionHeading = (text) =>
  new Paragraph({
    children: [new TextRun({ text: text.toUpperCase(), bold: true, size: 20 })],
    spacing: { before: 160, after: 60 },
    border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: "000000" } },
  });

// org (bold, left) ......... dates (right)
const entryHeader = (org, dates) =>
  line([
    new TextRun({ text: org, bold: true, size: 20 }),
    new TextRun({ text: "\t" + (dates || ""), size: 20 }),
  ]);

const entrySubheader = (title) =>
  line([new TextRun({ text: title, italics: true, size: 20 })]);

const bullet = (text) =>
  new Paragraph({
    children: [new TextRun({ text, size: 20 })],
    bullet: { level: 0 },
    spacing: { after: 20 },
  });

const children = [];

// Header
children.push(
  new Paragraph({
    children: [new TextRun({ text: c.name, bold: true, size: 32 })],
    alignment: AlignmentType.CENTER,
    spacing: { after: 40 },
  }),
  new Paragraph({
    children: [
      new TextRun({
        text: [c.location, c.phone, c.email, c.linkedin, c.website]
          .filter(Boolean)
          .join("  •  "),
        size: 18,
      }),
    ],
    alignment: AlignmentType.CENTER,
    spacing: { after: 80 },
  })
);

if (data.summary) {
  children.push(sectionHeading("Summary"));
  children.push(line([new TextRun({ text: data.summary, size: 20 })], { after: 40 }));
}

if (data.education?.length) {
  children.push(sectionHeading("Education"));
  for (const e of data.education) {
    children.push(entryHeader(e.org, e.dates));
    if (e.title) children.push(entrySubheader(e.title));
    for (const b of e.bullets || []) children.push(bullet(b));
  }
}

if (data.experience?.length) {
  children.push(sectionHeading("Experience"));
  for (const e of data.experience) {
    children.push(entryHeader(e.org, e.dates));
    children.push(entrySubheader(e.title));
    for (const b of e.bullets || []) children.push(bullet(b));
  }
}

if (data.projects?.length) {
  children.push(sectionHeading("Projects"));
  for (const p of data.projects) {
    children.push(entryHeader(p.org, p.dates));
    if (p.title) children.push(entrySubheader(p.title));
    for (const b of p.bullets || []) children.push(bullet(b));
  }
}

if (data.skills?.length) {
  children.push(sectionHeading("Skills"));
  children.push(line([new TextRun({ text: data.skills.join(", "), size: 20 })]));
}

const doc = new Document({
  styles: { default: { document: { run: { font: "Calibri" } } } },
  sections: [
    {
      properties: {
        page: { margin: { top: 720, right: 720, bottom: 720, left: 720 } },
      },
      children,
    },
  ],
});

Packer.toBuffer(doc).then((buf) => {
  fs.writeFileSync(outPath, buf);
  console.log(outPath);
});

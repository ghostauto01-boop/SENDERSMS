import{n as d,r as m,j as a,t as W,E as G,X as J,z as k}from"./index-lOmh8ccF.js";import{L as Z}from"./link-2-D2ZhbJpA.js";import{I as A,T as ee}from"./table-2-7Ra-zyKx.js";import{M as te}from"./mouse-pointer-click-D70AZEjH.js";import{M as ae}from"./minus-De2QC77X.js";import{P as E}from"./paperclip-DQkxg1sK.js";/**
 * @license lucide-react v0.460.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const se=d("Bold",[["path",{d:"M6 12h9a4 4 0 0 1 0 8H7a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1h7a4 4 0 0 1 0 8",key:"mg9rjx"}]]);/**
 * @license lucide-react v0.460.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const re=d("CodeXml",[["path",{d:"m18 16 4-4-4-4",key:"1inbqp"}],["path",{d:"m6 8-4 4 4 4",key:"15zrgr"}],["path",{d:"m14.5 4-5 16",key:"e7oirm"}]]);/**
 * @license lucide-react v0.460.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const ne=d("Heading1",[["path",{d:"M4 12h8",key:"17cfdx"}],["path",{d:"M4 18V6",key:"1rz3zl"}],["path",{d:"M12 18V6",key:"zqpxq5"}],["path",{d:"m17 12 3-2v8",key:"1hhhft"}]]);/**
 * @license lucide-react v0.460.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const ie=d("Heading2",[["path",{d:"M4 12h8",key:"17cfdx"}],["path",{d:"M4 18V6",key:"1rz3zl"}],["path",{d:"M12 18V6",key:"zqpxq5"}],["path",{d:"M21 18h-4c0-4 4-3 4-6 0-1.5-2-2.5-4-1",key:"9jr5yi"}]]);/**
 * @license lucide-react v0.460.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const le=d("Italic",[["line",{x1:"19",x2:"10",y1:"4",y2:"4",key:"15jd3p"}],["line",{x1:"14",x2:"5",y1:"20",y2:"20",key:"bu0au3"}],["line",{x1:"15",x2:"9",y1:"4",y2:"20",key:"uljnxc"}]]);/**
 * @license lucide-react v0.460.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const oe=d("ListOrdered",[["path",{d:"M10 12h11",key:"6m4ad9"}],["path",{d:"M10 18h11",key:"11hvi2"}],["path",{d:"M10 6h11",key:"c7qv1k"}],["path",{d:"M4 10h2",key:"16xx2s"}],["path",{d:"M4 6h1v4",key:"cnovpq"}],["path",{d:"M6 18H4c0-1 2-2 2-3s-1-1.5-2-1",key:"m9a95d"}]]);/**
 * @license lucide-react v0.460.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const ce=d("Quote",[["path",{d:"M16 3a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2 1 1 0 0 1 1 1v1a2 2 0 0 1-2 2 1 1 0 0 0-1 1v2a1 1 0 0 0 1 1 6 6 0 0 0 6-6V5a2 2 0 0 0-2-2z",key:"rib7q0"}],["path",{d:"M5 3a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2 1 1 0 0 1 1 1v1a2 2 0 0 1-2 2 1 1 0 0 0-1 1v2a1 1 0 0 0 1 1 6 6 0 0 0 6-6V5a2 2 0 0 0-2-2z",key:"1ymkrd"}]]);/**
 * @license lucide-react v0.460.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const de=d("Underline",[["path",{d:"M6 4v6a6 6 0 0 0 12 0V4",key:"9kb039"}],["line",{x1:"4",x2:"20",y1:"20",y2:"20",key:"nun2al"}]]),pe=4*1024*1024;function ke({body:r,onBody:o,html:c,onHtml:g,attachments:w,onAttachments:p,fillHeight:y=!1,bodyLabel:T="Email body",autoFocus:H=!1,variables:_=["{{first_name}}","{{business_name}}","{{city}}","{{state}}","{{website}}","{{industry}}"]}){const x=w??[],h=m.useRef(null),[f,q]=m.useState(!1),[v,B]=m.useState(!1),[M,N]=m.useState(""),[z,S]=m.useState(""),[P,L]=m.useState(!1),[R,C]=m.useState(!1),b=(e,t,s)=>{const n=h.current;if(!n){o(r+e+s+t);return}const i=n.selectionStart??r.length,l=n.selectionEnd??i,u=r.slice(i,l)||s,K=r.slice(0,i)+e+u+t+r.slice(l);o(K),requestAnimationFrame(()=>{n.focus(),n.setSelectionRange(i+e.length,i+e.length+u.length)})},$=e=>{const t=h.current,s=(t==null?void 0:t.selectionStart)??r.length,n=`${r.slice(0,s)}${e}${r.slice(s)}`;o(n),requestAnimationFrame(()=>{t==null||t.focus(),t==null||t.setSelectionRange(s+e.length,s+e.length)})},U=()=>{const e=r.split(/\n{2,}/).map(t=>`<p>${t.replace(/\n/g,"<br>")}</p>`).join(`
`);g(e),k.success("HTML regenerated from the plain-text body")},V=()=>{const e=M.trim();if(!/^https?:\/\//i.test(e)){k.error("Paste an https:// image link");return}const t=h.current,s=(t==null?void 0:t.selectionStart)??r.length,n=`[image: ${e}]`;g(`${c}
<p><img src="${e}" alt="" style="max-width:100%;height:auto" /></p>`),o(`${r.slice(0,s)}${n}
${r.slice(s)}`),N(""),L(!1),k.success("Image added to the HTML body")},j=(e,t)=>{const s=h.current,n=(s==null?void 0:s.selectionStart)??r.length;g(`${c}${c?`
`:""}${e}`),t&&o(`${r.slice(0,n)}${t}
${r.slice(n)}`)},I=e=>{const t=h.current,s=(t==null?void 0:t.selectionStart)??r.length,n=(t==null?void 0:t.selectionEnd)??s,i=r.slice(s,n)||"Your headline",l=`${r.slice(0,s)}${i}
${r.slice(n)}`;o(l),g(`${c}
<h${e} style="margin:0 0 12px">${i}</h${e}>`)},F=()=>{const e=window.prompt("Where should the button link to?","https://");if(!e)return;const t=window.prompt("Button label","Book a call")||"Click here";j(`<p style="margin:18px 0"><a href="${e}" style="background:#123c2b;color:#ffffff;padding:12px 22px;border-radius:6px;text-decoration:none;display:inline-block;font-weight:600">${t}</a></p>`,`${t}: ${e}`)},Q=()=>j('<hr style="border:none;border-top:1px solid #e7e8e4;margin:20px 0">',"----------"),X=()=>{const t=[["Item","Quantity","Price"],["","",""],["","",""]].map((s,n)=>`<tr>${s.map(i=>n===0?`<th style="border:1px solid #e7e8e4;padding:8px;text-align:left">${i}</th>`:`<td style="border:1px solid #e7e8e4;padding:8px">${i}</td>`).join("")}</tr>`).join("");j(`<table style="border-collapse:collapse;width:100%;margin:16px 0" role="presentation">${t}</table>`,"Item / Quantity / Price table added to the HTML body")},D=()=>{const e=z.trim()||"https://",t=h.current,s=(t==null?void 0:t.selectionStart)??r.length,n=(t==null?void 0:t.selectionEnd)??s,i=r.slice(s,n)||e,l=`${r.slice(0,s)}${i} (${e})${r.slice(n)}`;o(l),g(`${c}
<p><a href="${e}">${i}</a></p>`),S(""),C(!1)},O=async e=>{if(!(e!=null&&e.length))return;const t=[];for(const s of Array.from(e)){if(s.size>pe){k.error(`${s.name} is larger than 4MB and was skipped`);continue}const n=await s.arrayBuffer();let i="";const l=new Uint8Array(n);for(let u=0;u<l.length;u+=1)i+=String.fromCharCode(l[u]);t.push({name:s.name,content_type:s.type||"application/octet-stream",size:s.size,content_base64:btoa(i)})}t.length&&(p==null||p([...w??[],...t].slice(0,10)),k.success(`${t.length} file(s) attached`))},Y=[{icon:se,title:"Bold",run:()=>b("**","**","bold text")},{icon:le,title:"Italic",run:()=>b("_","_","italic text")},{icon:de,title:"Underline",run:()=>b("<u>","</u>","underlined")},{icon:W,title:"Bullet list",run:()=>$(`
• item one
• item two
`)},{icon:oe,title:"Numbered list",run:()=>$(`
1. first
2. second
`)},{icon:ce,title:"Quote",run:()=>b(`
> `,`
`,"quoted text")},{icon:ne,title:"Heading",run:()=>I(1)},{icon:ie,title:"Sub-heading",run:()=>I(2)},{icon:Z,title:"Insert link",run:()=>C(e=>!e)},{icon:A,title:"Insert image (by link)",run:()=>L(e=>!e)},{icon:te,title:"Button / call to action",run:F},{icon:ee,title:"Table (e.g. a price list)",run:X},{icon:ae,title:"Divider",run:Q}];return a.jsxs("div",{className:y?"h-full min-h-0 flex flex-col gap-2":"space-y-2",children:[a.jsxs("div",{className:"shrink-0 flex flex-wrap items-center gap-1 rounded-lg border border-gray-200 dark:border-gray-700 p-1",role:"toolbar","aria-label":"Email formatting",children:[Y.map(({icon:e,title:t,run:s})=>a.jsx("button",{type:"button",title:t,"aria-label":t,onClick:s,className:"p-1.5 rounded text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700",children:a.jsx(e,{size:15})},t)),p&&a.jsxs(a.Fragment,{children:[a.jsx("span",{className:"w-px h-5 bg-gray-200 dark:bg-gray-700 mx-1"}),a.jsxs("label",{className:"p-1.5 rounded text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700 cursor-pointer",title:"Attach files",children:[a.jsx(E,{size:15}),a.jsx("input",{type:"file","aria-label":"Attach files",multiple:!0,className:"hidden",onChange:e=>{O(e.target.files),e.target.value=""}})]})]}),a.jsx("button",{type:"button",title:"Edit the raw HTML","aria-label":"Edit the raw HTML","aria-pressed":f,onClick:()=>q(e=>!e),className:`p-1.5 rounded ${f?"text-primary-600 bg-primary-50 dark:bg-primary-900/20":"text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700"}`,children:a.jsx(re,{size:15})}),a.jsx("button",{type:"button",title:"Preview what the recipient sees","aria-label":"Preview what the recipient sees","aria-pressed":v,onClick:()=>B(e=>!e),className:`p-1.5 rounded ${v?"text-primary-600 bg-primary-50 dark:bg-primary-900/20":"text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700"}`,children:a.jsx(G,{size:15})}),a.jsx("button",{type:"button",className:"ml-auto text-xs text-gray-400 hover:text-primary-600 px-2",onClick:U,children:"Text → HTML"})]}),R&&a.jsxs("div",{className:"flex gap-2",children:[a.jsx("input",{className:"input text-sm",placeholder:"https://your-site.com/page",value:z,onChange:e=>S(e.target.value)}),a.jsx("button",{type:"button",className:"btn-secondary text-sm",onClick:D,children:"Add link"})]}),P&&a.jsxs("div",{className:"flex gap-2",children:[a.jsx("input",{className:"input text-sm",placeholder:"https://cdn.your-site.com/photo.png",value:M,onChange:e=>N(e.target.value)}),a.jsx("button",{type:"button",className:"btn-secondary text-sm",onClick:V,children:"Add image"})]}),a.jsx("div",{className:y?"flex-1 min-h-[160px] flex flex-col":"",children:f?a.jsx("textarea",{"aria-label":"HTML email body",className:`input w-full resize-y ${y?"flex-1 min-h-[160px]":"min-h-[160px]"} font-mono text-xs`,value:c,onChange:e=>g(e.target.value),placeholder:"<p>Hi {{first_name}},</p>"}):a.jsx("textarea",{ref:h,"aria-label":T,autoFocus:H,className:`input w-full resize-y ${y?"flex-1 min-h-[160px]":"min-h-[160px]"}`,value:r,onChange:e=>o(e.target.value),placeholder:`Hi {{first_name}},

…`})}),a.jsxs("div",{className:"shrink-0 flex flex-wrap items-center gap-1",children:[a.jsx("span",{className:"text-xs text-gray-400 mr-1",children:"Insert:"}),_.map(e=>a.jsx("button",{type:"button",onClick:()=>$(e),className:"text-xs px-2 py-0.5 bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 rounded font-mono text-gray-600 dark:text-gray-300",children:e},e))]}),x.length>0&&a.jsx("div",{className:"shrink-0 space-y-1 max-h-28 overflow-y-auto",children:x.map((e,t)=>a.jsxs("div",{className:"flex items-center gap-2 text-xs text-gray-600 dark:text-gray-300",children:[a.jsx(E,{size:12}),a.jsx("span",{className:"truncate",children:e.name}),a.jsx("span",{className:"text-gray-400",children:e.size?`${Math.max(1,Math.round(e.size/1024))} KB`:""}),a.jsx("button",{type:"button",className:"text-gray-400 hover:text-danger-500","aria-label":`Remove ${e.name}`,onClick:()=>p==null?void 0:p(x.filter((s,n)=>n!==t)),children:a.jsx(J,{size:12})})]},`${e.name}-${t}`))}),v&&a.jsxs("div",{className:`shrink-0 rounded-lg border border-gray-200 dark:border-gray-700 p-3 bg-gray-50 dark:bg-gray-900/40 ${y?"max-h-[35vh] overflow-y-auto":""}`,children:[a.jsx("p",{className:"text-xs text-gray-500 mb-2",children:"Preview"}),c.trim()?a.jsx("div",{className:"prose prose-sm max-w-none dark:prose-invert",dangerouslySetInnerHTML:{__html:c}}):a.jsx("p",{className:"whitespace-pre-wrap text-sm",children:r}),x.length>0&&a.jsxs("p",{className:"text-xs text-gray-500 mt-3",children:[x.length," attachment",x.length===1?"":"s",":"," ",x.map(e=>e.name).join(", ")]})]})]})}export{ke as R};

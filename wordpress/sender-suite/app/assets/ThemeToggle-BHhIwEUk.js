import{o as t,a2 as d,j as a}from"./index-BqW6Waa9.js";/**
 * @license lucide-react v0.460.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const c=t("Monitor",[["rect",{width:"20",height:"14",x:"2",y:"3",rx:"2",key:"48i651"}],["line",{x1:"8",x2:"16",y1:"21",y2:"21",key:"1svkeh"}],["line",{x1:"12",x2:"12",y1:"17",y2:"21",key:"vw1qmm"}]]);/**
 * @license lucide-react v0.460.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const h=t("Moon",[["path",{d:"M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z",key:"a7tn18"}]]);/**
 * @license lucide-react v0.460.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const y=t("Sun",[["circle",{cx:"12",cy:"12",r:"4",key:"4exip2"}],["path",{d:"M12 2v2",key:"tus03m"}],["path",{d:"M12 20v2",key:"1lh1kg"}],["path",{d:"m4.93 4.93 1.41 1.41",key:"149t6j"}],["path",{d:"m17.66 17.66 1.41 1.41",key:"ptbguv"}],["path",{d:"M2 12h2",key:"1t8f8n"}],["path",{d:"M20 12h2",key:"1q8mjw"}],["path",{d:"m6.34 17.66-1.41 1.41",key:"1m8zz5"}],["path",{d:"m19.07 4.93-1.41 1.41",key:"1shlcs"}]]),m=[{value:"light",label:"Light",icon:y},{value:"dark",label:"Dark",icon:h},{value:"system",label:"System",icon:c}];function g({className:i="",showLabel:l=!0}){const{mode:s,setMode:o}=d();return a.jsx("div",{className:`inline-flex items-center gap-1 rounded-full border border-gray-200/80 dark:border-gray-700/70 bg-white/80 dark:bg-gray-800/70 p-1 shadow-card backdrop-blur ${i}`,role:"radiogroup","aria-label":"Appearance",children:m.map(e=>{const r=s===e.value,n=e.icon;return a.jsxs("button",{type:"button",role:"radio","aria-checked":r,onClick:()=>o(e.value),title:`${e.label} mode`,className:`inline-flex items-center gap-1.5 rounded-full px-3 min-h-[36px] text-[12.5px] font-semibold
                        transition-all duration-200 ease-gentle active:scale-[0.97] focus-visible:ring-2
                        focus-visible:ring-primary-500/50 ${r?"bg-primary-900 text-white shadow-sm shadow-primary-950/20 dark:bg-primary-600":"text-gray-500 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white hover:bg-gray-100/80 dark:hover:bg-gray-700/50"}`,children:[a.jsx(n,{size:14,strokeWidth:r?2.2:1.8}),l&&a.jsx("span",{children:e.label})]},e.value)})})}export{h as M,y as S,g as T};

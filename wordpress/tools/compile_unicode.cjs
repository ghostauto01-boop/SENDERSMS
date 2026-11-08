'use strict';
const fs=require('node:fs'),path=require('node:path');const base=path.resolve(__dirname,'../tooling/node_modules/@unicode/unicode-15.0.0');const load=p=>require(base+'/'+p+'/code-points.js');
const lower={};for(const[a,b]of load('Simple_Case_Mapping/Lowercase'))lower[a]=[b];for(const[a,b]of load('Special_Casing/Lowercase'))lower[a]=b;
const ranges=points=>{const out=[];for(const p of points){if(out.length&&out.at(-1)[1]+1===p)out.at(-1)[1]=p;else out.push([p,p]);}return out;};
const decimal={};
// Nd ranges are ordered blocks of ten; adjacent styled blocks remain multiples of ten.
for(const[start,end]of ranges(load('General_Category/Decimal_Number')))for(let p=start;p<=end;p++)decimal[p]=(p-start)%10;
fs.writeFileSync(path.resolve(__dirname,'../sender-suite/schema/unicode15.json'),JSON.stringify({version:'15.0.0',lower,cased:ranges(load('Binary_Property/Cased')),ignorable:ranges(load('Binary_Property/Case_Ignorable')),decimal})+'\n');

const a=new Map;function o(n,e){let t=a.get(n);return t||(t=new Set,a.set(n,t)),t.add(e),()=>{t.delete(e)}}function s(n){const e=a.get(n);e&&[...e].forEach(t=>{try{t()}catch{}})}export{s as n,o};

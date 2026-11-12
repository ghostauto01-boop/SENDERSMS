function r(s){const n=/[^\x00-\x7F]/.test(s),t=s.length,e=n?70:160,c=n?67:153,o=t===0?0:t<=e?1:Math.ceil(t/c);return{chars:t,segments:o,unicode:n}}export{r as s};

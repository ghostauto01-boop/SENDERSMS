import { defineConfig } from 'vitest/config';
import { fileURLToPath } from 'node:url';
export default defineConfig({test:{include:['wordpress/ui/tests/*.test.ts'],environment:'jsdom',environmentOptions:{jsdom:{url:'https://native.example/sub/?sender_suite_app=1#/contacts'}}},resolve:{alias:{'@native-runtime':fileURLToPath(new URL('./overlay/runtime.ts',import.meta.url))}}});

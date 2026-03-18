import { defineConfig } from 'wxt';

export default defineConfig({
  manifest: {
    name: 'Agentic Browser',
    description: 'AI-powered browser agent that understands pages and performs actions autonomously',
    version: '0.1.0',
    permissions: ['activeTab', 'sidePanel', 'storage', 'scripting'],
    host_permissions: ['<all_urls>'],
    side_panel: {
      default_path: 'sidepanel.html',
    },
  },
});

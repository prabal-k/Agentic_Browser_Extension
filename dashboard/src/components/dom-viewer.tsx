"use client";

import { useState, useRef } from "react";
import { useAgentStore } from "@/stores/agent-store";
import { cn } from "@/lib/utils";
import { Upload, X, Eye, EyeOff, Globe } from "lucide-react";
import type { DOMSnapshot } from "@/lib/types";

function ElementRow({ el }: { el: DOMSnapshot["elements"][0] }) {
  const typeColors: Record<string, string> = {
    button: "text-orange-400",
    link: "text-blue-400",
    text_input: "text-green-400",
    textarea: "text-green-300",
    select: "text-purple-400",
    checkbox: "text-yellow-400",
    heading: "text-gray-300",
    paragraph: "text-gray-500",
    image: "text-pink-400",
  };

  return (
    <div className={cn(
      "flex items-start gap-2 py-1 px-2 text-xs font-mono hover:bg-gray-800/50 rounded",
      !el.is_visible && "opacity-40"
    )}>
      <span className="text-gray-500 w-6 text-right shrink-0">[{el.element_id}]</span>
      <span className={cn("shrink-0", typeColors[el.element_type] || "text-gray-400")}>
        {el.element_type}
      </span>
      <span className="text-gray-300 truncate">
        {el.text ? `"${el.text.slice(0, 60)}"` : ""}
      </span>
      {el.attributes.placeholder && !el.text && (
        <span className="text-gray-600 truncate">placeholder=&quot;{el.attributes.placeholder}&quot;</span>
      )}
      {el.parent_context && (
        <span className="text-gray-600 ml-auto shrink-0">[{el.parent_context}]</span>
      )}
    </div>
  );
}

export function DomViewer() {
  const { domSnapshot, setDomSnapshot, showDomViewer, setShowDomViewer } = useAgentStore();
  const [dragActive, setDragActive] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const handleFile = (file: File) => {
    const reader = new FileReader();
    reader.onload = (e) => {
      try {
        const data = JSON.parse(e.target?.result as string) as DOMSnapshot;
        setDomSnapshot(data);
      } catch {
        alert("Invalid JSON file");
      }
    };
    reader.readAsText(file);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragActive(false);
    const file = e.dataTransfer.files[0];
    if (file) handleFile(file);
  };

  if (!showDomViewer) {
    return (
      <button
        onClick={() => setShowDomViewer(true)}
        className="flex items-center gap-1.5 px-3 py-1.5 text-xs text-gray-400
                   hover:text-gray-200 transition-colors"
      >
        <Eye size={14} />
        DOM Viewer
        {domSnapshot && <span className="text-green-400 ml-1">({domSnapshot.elements.length} elements)</span>}
      </button>
    );
  }

  return (
    <div className="border-l border-gray-800 flex flex-col w-96">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-gray-800">
        <div className="flex items-center gap-2">
          <Globe size={14} className="text-blue-400" />
          <span className="text-sm font-medium text-gray-300">DOM Snapshot</span>
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={() => fileRef.current?.click()}
            className="p-1 text-gray-400 hover:text-gray-200 transition-colors"
            title="Upload snapshot"
          >
            <Upload size={14} />
          </button>
          {domSnapshot && (
            <button
              onClick={() => setDomSnapshot(null)}
              className="p-1 text-gray-400 hover:text-red-400 transition-colors"
              title="Clear snapshot"
            >
              <X size={14} />
            </button>
          )}
          <button
            onClick={() => setShowDomViewer(false)}
            className="p-1 text-gray-400 hover:text-gray-200 transition-colors"
            title="Hide viewer"
          >
            <EyeOff size={14} />
          </button>
        </div>
      </div>

      <input
        ref={fileRef}
        type="file"
        accept=".json"
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) handleFile(file);
        }}
      />

      {/* Content */}
      {!domSnapshot ? (
        <div
          className={cn(
            "flex-1 flex items-center justify-center p-6",
            dragActive && "bg-blue-950/20"
          )}
          onDragOver={(e) => { e.preventDefault(); setDragActive(true); }}
          onDragLeave={() => setDragActive(false)}
          onDrop={handleDrop}
        >
          <div className="text-center text-gray-500 text-sm">
            <Upload size={24} className="mx-auto mb-2 opacity-50" />
            <p>Drop a DOM snapshot JSON file here</p>
            <p className="text-xs mt-1">or click the upload button</p>
          </div>
        </div>
      ) : (
        <div className="flex-1 overflow-y-auto">
          {/* Page info */}
          <div className="px-3 py-2 border-b border-gray-800 text-xs space-y-1">
            <p className="text-gray-300 font-medium truncate">{domSnapshot.title}</p>
            <p className="text-blue-400 truncate">{domSnapshot.url}</p>
            <p className="text-gray-500">
              {domSnapshot.elements.length} elements |{" "}
              {domSnapshot.viewport_width}x{domSnapshot.viewport_height} |{" "}
              scroll: {Math.round(domSnapshot.scroll_position * 100)}%
            </p>
          </div>

          {/* Elements list */}
          <div className="p-2 space-y-0.5">
            {domSnapshot.elements.map((el) => (
              <ElementRow key={el.element_id} el={el} />
            ))}
          </div>

          {/* Forms */}
          {domSnapshot.forms && domSnapshot.forms.length > 0 && (
            <div className="px-3 py-2 border-t border-gray-800">
              <p className="text-xs text-gray-500 mb-1">Forms</p>
              {domSnapshot.forms.map((form, i) => (
                <p key={i} className="text-xs text-gray-400">
                  {form.name} ({form.method} {form.action}) fields: [{form.field_ids.join(", ")}]
                </p>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

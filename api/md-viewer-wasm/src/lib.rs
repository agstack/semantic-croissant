#![cfg(target_arch = "wasm32")]

use eframe::egui;
use wasm_bindgen::prelude::*;
use egui_commonmark_extended::{CommonMarkViewer, CommonMarkCache};

#[derive(Default)]
struct MdViewerApp {
    cache: CommonMarkCache,
    markdown: String,
}

impl MdViewerApp {
    pub fn new(cc: &eframe::CreationContext<'_>) -> Self {
        let mut app = Self::default();
        
        // Try to get markdown content from global JS variable
        let window = web_sys::window().unwrap();
        if let Some(md) = window.get("vaultMarkdownContent") {
            if let Some(md_str) = md.as_string() {
                app.markdown = md_str;
            }
        }
        
        app
    }
}

impl eframe::App for MdViewerApp {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        egui::CentralPanel::default().show(ctx, |ui| {
            egui::ScrollArea::vertical().show(ui, |ui| {
                CommonMarkViewer::new().show(ui, &mut self.cache, &self.markdown);
            });
        });
    }
}

#[wasm_bindgen(start)]
pub async fn start() -> Result<(), JsValue> {
    console_error_panic_hook::set_once();

    let web_options = eframe::WebOptions::default();
    eframe::WebRunner::new()
        .start(
            "the_canvas_id",
            web_options,
            Box::new(|cc| Ok(Box::new(MdViewerApp::new(cc)))),
        )
        .await?;

    Ok(())
}

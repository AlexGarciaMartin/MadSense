# Regenerates the installer artwork without installing fonts or modifying Windows.
# Uses the same logo polygons, palette and Anton typeface as the MadSense website.
[CmdletBinding()]
param([string]$OutputDirectory)
$ErrorActionPreference = 'Stop'
if (-not $OutputDirectory) { $OutputDirectory = Join-Path (Split-Path -Parent $PSScriptRoot) 'setup\assets' }
Add-Type -AssemblyName System.Drawing
$fontPath = Join-Path $OutputDirectory 'Anton-Regular.ttf'
if (-not (Test-Path -LiteralPath $fontPath)) { throw "Missing licensed font: $fontPath" }
New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
Add-Type -ReferencedAssemblies System.Drawing -TypeDefinition @'
using System;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
using System.Drawing.Text;
using System.IO;

public static class MadSenseInstallerArt
{
    static readonly Color Paper = ColorTranslator.FromHtml("#f0ebdc");
    static readonly Color Ink = ColorTranslator.FromHtml("#181815");
    static readonly Color Orange = ColorTranslator.FromHtml("#f95428");
    static readonly Color Acid = ColorTranslator.FromHtml("#dcf131");
    static PrivateFontCollection fonts;

    static PointF[] Points(params float[] p) {
        var r=new PointF[p.Length/2];
        for(int i=0;i<r.Length;i++) r[i]=new PointF(p[i*2],p[i*2+1]);
        return r;
    }
    static void Polygon(Graphics g, Color color, params float[] p) {
        using(var b=new SolidBrush(color)) g.FillPolygon(b,Points(p));
    }
    static void Rect(Graphics g, Color color, float x,float y,float w,float h) {
        using(var b=new SolidBrush(color)) g.FillRectangle(b,x,y,w,h);
    }
    static void Text(Graphics g,string value,float size,Color color,float x,float y) {
        using(var f=new Font(fonts.Families[0],size,FontStyle.Regular,GraphicsUnit.Pixel))
        using(var b=new SolidBrush(color))
        using(var fmt=(StringFormat)StringFormat.GenericTypographic.Clone()) {
            fmt.FormatFlags |= StringFormatFlags.NoWrap;
            g.DrawString(value,f,b,new PointF(x,y),fmt);
        }
    }
    static void Logo(Graphics g,float x,float y,float size,float angle,bool shadow) {
        var s=g.Save(); g.TranslateTransform(x+size/2,y+size/2); g.RotateTransform(angle);
        g.TranslateTransform(-size/2,-size/2); g.ScaleTransform(size/64,size/64);
        if(shadow) Rect(g,Ink,3,3,64,64);
        Rect(g,Orange,0,0,64,64);
        // Original src/assets/madsense.svg; no reinterpretation of the mark.
        Polygon(g,Ink,3,51,13,10,27,26,36,8,47,24,58,11,59,52,46,52,46,34,36,48,25,35,17,54);
        Polygon(g,Acid,23,23,34,15,29,31,40,28,29,47,31,34,20,37);
        g.Restore(s);
    }
    static void Sidebar(Graphics g) {
        g.Clear(Paper);
        // Screen-print dots and offset blocks deliberately remain flat, like the app.
        using(var b=new SolidBrush(Color.FromArgb(45,Ink)))
            for(int y=9;y<136;y+=10) for(int x=8;x<164;x+=10) g.FillEllipse(b,x,y,1.3f,1.3f);
        Logo(g,30,25,104,-6,true);
        Text(g,"MADSENSE",26,Ink,14,138);
        Rect(g,Ink,0,176,164,138);
        Polygon(g,Ink,0,170,164,176,0,176);
        Text(g,"ENCIENDE.",29,Paper,13,184);
        Text(g,"JUEGA.",36,Paper,13,216);
        Polygon(g,Acid,9,267,156,262,153,296,7,298);
        Text(g,"SIN CABLE.",28,Ink,14,260);
        for(int i=0;i<9;i++) Rect(g,i%3==0?Orange:Paper,15+i*15,307,i%3==0?6:2,3);
    }
    static void Small(Graphics g) { g.Clear(Paper); Logo(g,6,6,42,-6,false); }
    static void Header(Graphics g) {
        g.Clear(Paper); Logo(g,16,12,54,-6,false);
        Text(g,"MADSENSE",38,Ink,83,15);
        Rect(g,Ink,0,80,600,4);
        Polygon(g,Orange,492,0,600,0,600,80,465,80);
        using(var b=new SolidBrush(Color.FromArgb(65,Ink)))
            for(int y=8;y<79;y+=10) for(int x=500;x<600;x+=10) g.FillEllipse(b,x,y,2,2);
        Polygon(g,Acid,528,23,583,20,579,53,524,56);
        Text(g,"PC",23,Ink,542,21);
    }
    static void Save(string dir,string name,int width,int height,int scale,Action<Graphics> draw) {
        // Render at 3x the target resolution and downsample once for readable small type.
        using(var master=new Bitmap(width*scale*3,height*scale*3,PixelFormat.Format24bppRgb)) {
            using(var g=Graphics.FromImage(master)) {
                g.SmoothingMode=SmoothingMode.AntiAlias;
                g.TextRenderingHint=TextRenderingHint.AntiAliasGridFit;
                g.ScaleTransform(scale*3,scale*3); draw(g);
            }
            using(var bitmap=new Bitmap(width*scale,height*scale,PixelFormat.Format24bppRgb)) {
                using(var g=Graphics.FromImage(bitmap)) {
                    g.InterpolationMode=InterpolationMode.HighQualityBicubic;
                    g.PixelOffsetMode=PixelOffsetMode.HighQuality;
                    g.DrawImage(master,new Rectangle(0,0,bitmap.Width,bitmap.Height));
                }
                var suffix=scale==1?"":"@"+scale+"x";
                bitmap.Save(Path.Combine(dir,name+suffix+".bmp"),ImageFormat.Bmp);
                if(scale==2) bitmap.Save(Path.Combine(dir,name+"-preview.png"),ImageFormat.Png);
            }
        }
    }
    static void SaveIcon(string directory) {
        int[] sizes={16,20,24,32,40,48,64,128,256};
        var images=new byte[sizes.Length][];
        for(int i=0;i<sizes.Length;i++) {
            int n=sizes[i];
            using(var master=new Bitmap(n*3,n*3,PixelFormat.Format32bppArgb))
            using(var bitmap=new Bitmap(n,n,PixelFormat.Format32bppArgb)) {
                using(var g=Graphics.FromImage(master)) {
                    g.SmoothingMode=SmoothingMode.AntiAlias;
                    Logo(g,0,0,n*3,0,false);
                }
                using(var g=Graphics.FromImage(bitmap)) {
                    g.InterpolationMode=InterpolationMode.HighQualityBicubic;
                    g.PixelOffsetMode=PixelOffsetMode.HighQuality;
                    g.DrawImage(master,new Rectangle(0,0,n,n));
                }
                using(var stream=new MemoryStream()) {
                    bitmap.Save(stream,ImageFormat.Png); images[i]=stream.ToArray();
                }
            }
        }
        using(var file=File.Create(Path.Combine(directory,"installer.ico")))
        using(var writer=new BinaryWriter(file)) {
            writer.Write((ushort)0); writer.Write((ushort)1); writer.Write((ushort)sizes.Length);
            int offset=6+16*sizes.Length;
            for(int i=0;i<sizes.Length;i++) {
                writer.Write((byte)(sizes[i]==256?0:sizes[i])); writer.Write((byte)(sizes[i]==256?0:sizes[i]));
                writer.Write((byte)0); writer.Write((byte)0); writer.Write((ushort)1); writer.Write((ushort)32);
                writer.Write(images[i].Length); writer.Write(offset); offset+=images[i].Length;
            }
            foreach(var image in images) writer.Write(image);
        }
    }
    public static void Generate(string output,string font) {
        using(fonts=new PrivateFontCollection()) {
            fonts.AddFontFile(font);
            for(int scale=1;scale<=3;scale++) {
                Save(output,"wizard-large",164,314,scale,Sidebar);
                Save(output,"wizard-small",55,55,scale,Small);
            }
            Save(output,"brand-header",600,84,1,Header);
            Save(output,"brand-header",600,84,2,Header);
            SaveIcon(output);
        }
    }
}
'@
[MadSenseInstallerArt]::Generate([IO.Path]::GetFullPath($OutputDirectory),[IO.Path]::GetFullPath($fontPath))
Get-ChildItem -LiteralPath $OutputDirectory -File | Where-Object { $_.Extension -in '.bmp','.png' } | Select-Object Name,Length

# 作業タブ(タブグループ「shopify自動発送ツール」内のPlus Shipping)を表に出し、Edgeを前面にする。
# 裏タブ/隠れたウィンドウではPlus Shippingのiframeが止まり、クリックが効かないため。
#   powershell -File surface_tab.ps1            作業タブを表へ
#   powershell -File surface_tab.ps1 -Name "調整さん"   名前に一致するタブへ戻す
param([string]$Name = "")
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
Add-Type @"
using System; using System.Runtime.InteropServices;
public class SurfW {
 [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c);
 [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
 [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
 [DllImport("user32.dll")] public static extern void keybd_event(byte k, byte s, uint f, UIntPtr e);
}
"@
$edge = Get-Process msedge | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
$h = $edge.MainWindowHandle
$root = [System.Windows.Automation.AutomationElement]::FromHandle($h)
$cond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::TabItem)
$tabs = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants, $cond)
$cur = ""
foreach ($t in $tabs) { $s = $null; if ($t.TryGetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern, [ref]$s)) { if ($s.Current.IsSelected) { $cur = $t.Current.Name } } }
if ($Name) { $target = $tabs | Where-Object { $_.Current.Name -like "*$Name*" } | Select-Object -First 1 }
else { $target = $tabs | Where-Object { $_.Current.Name -like "CLAUDE-WORK*" } | Select-Object -First 1
  if (-not $target) { $target = $tabs | Where-Object { $_.Current.Name -like "*自動発送*" -and ($_.Current.Name -like "*Shopify*" -or $_.Current.Name -like "*B2_OKURIJYO*" -or $_.Current.Name -like "*amazonaws*") } | Select-Object -First 1 } }
$p = $null
if ($target -and $target.TryGetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern, [ref]$p)) { $p.Select(); $r = "OK" } else { $r = "NG(タブが見つからない)" }
if (-not $Name) {
  if ([SurfW]::IsIconic($h)) { [SurfW]::ShowWindow($h, 9) | Out-Null }
  [SurfW]::keybd_event(0x12,0,0,[UIntPtr]::Zero); [SurfW]::keybd_event(0x12,0,2,[UIntPtr]::Zero)
  [SurfW]::SetForegroundWindow($h) | Out-Null
}
$c = $cur.Substring(0, [Math]::Min(40, $cur.Length))
"$r / 直前のタブ: $c"

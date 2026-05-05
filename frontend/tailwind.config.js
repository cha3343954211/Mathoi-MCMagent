/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        ink: { 50:'#f7f7f6',100:'#eeeeec',200:'#d6d6d2',300:'#a8a8a2',400:'#75756f',500:'#4a4a45',600:'#33332f',700:'#22221f',800:'#161614',900:'#0a0a09' }
      },
      fontFamily: { sans: ['Inter','-apple-system','Segoe UI','PingFang SC','Microsoft Yahei','sans-serif'] }
    }
  },
  plugins: []
}

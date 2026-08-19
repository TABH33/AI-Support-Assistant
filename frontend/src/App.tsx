import './App.css'

function App() {
  return (
    <main className="flex flex-col items-center justify-center min-h-screen bg-gradient-to-br from-blue-50 to-indigo-100 dark:from-gray-900 dark:to-gray-800">
      <div className="text-center px-6">
        <h1 className="text-5xl font-bold text-gray-900 dark:text-white mb-4">
          Telematics AI Assistant
        </h1>
        <p className="text-xl text-gray-600 dark:text-gray-300 mb-8">
          AI-Driven Support for Fleet Management
        </p>
        <p className="text-lg text-gray-500 dark:text-gray-400 mb-12">
          Welcome to the intelligent telematics platform. Coming soon.
        </p>
        <button
          className="px-8 py-3 bg-indigo-600 hover:bg-indigo-700 text-white font-semibold rounded-lg transition duration-200"
          onClick={() => alert('Get started feature coming soon!')}
        >
          Get Started
        </button>
      </div>
    </main>
  )
}

export default App
